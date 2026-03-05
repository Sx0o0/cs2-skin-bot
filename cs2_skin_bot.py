import csv
import os
from datetime import datetime, timezone
from typing import List, Dict, Tuple
from urllib.parse import quote

import requests

# -----------------------------
# CONFIG
# -----------------------------
SKINPORT_HISTORY_URL = "https://api.skinport.com/v1/sales/history"
APP_ID = 730
CURRENCY = "BRL"

HISTORY_FILE = "skin_history.csv"
OUTPUT_HTML = "index.html"

TOTAL_ITEMS = 500
STAT_TRAK_QUOTA = 100

ALLOWED_WEAR = ("(Field-Tested)", "(Minimal Wear)", "(Factory New)")
MIN_VOL_30D = 10
MIN_CUR_PRICE = 1.0
MAX_CUR_PRICE = 10000.0
MAX_EXPECTED_PROFIT_PCT = 35.0

# Opcional: imagens reais via csgoskins.gg (exige API key)
CSGOSKINS_API_KEY = os.getenv("CSGOSKINS_API_KEY", "").strip()
CSGOSKINS_BASIC_DETAILS_URL = "https://csgoskins.gg/api/v1/basic-item-details"  # requer auth :contentReference[oaicite:2]{index=2}
IMAGE_CACHE_FILE = "image_cache.csv"  # cache local (evita reconsultar sempre)

# -----------------------------
# HELPERS
# -----------------------------
def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))

def is_allowed_wear(name: str) -> bool:
    return any(w in name for w in ALLOWED_WEAR)

def is_stattrak(name: str) -> bool:
    return name.startswith("StatTrak™ ")

def safe_num(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except:
        return default

def get_window(item: dict, key: str) -> dict:
    w = item.get(key)
    return w if isinstance(w, dict) else {}

def current_price(item: dict) -> float:
    # usa avg 24h, se não tiver usa avg 7d
    p24 = safe_num(get_window(item, "last_24_hours").get("avg"), 0.0)
    if p24 > 0:
        return p24
    p7 = safe_num(get_window(item, "last_7_days").get("avg"), 0.0)
    return p7

def speed_pct_per_day(item: dict) -> float:
    # velocidade aproximada a partir da amplitude 7d
    w7 = get_window(item, "last_7_days")
    mn = safe_num(w7.get("min"), 0.0)
    mx = safe_num(w7.get("max"), 0.0)
    avg = safe_num(w7.get("avg"), 0.0)
    if avg <= 0 or mx <= 0 or mn <= 0 or mx < mn:
        return 0.0
    amp_pct = ((mx - mn) / avg) * 100.0
    return clamp(amp_pct / 7.0, 0.05, 15.0)

def expected_profit_pct(cur: float, avg30: float) -> float:
    if cur <= 0 or avg30 <= 0:
        return 0.0
    raw = ((avg30 - cur) / cur) * 100.0
    raw = max(0.0, raw)
    return round(clamp(raw, 0.0, MAX_EXPECTED_PROFIT_PCT), 2)

def hold_days_estimate(cur: float, avg30: float, speed_day: float) -> int:
    if cur <= 0 or avg30 <= 0:
        return 21
    gap = ((avg30 - cur) / cur) * 100.0
    gap = max(0.0, gap)
    if gap < 0.8:
        return 3
    if speed_day <= 0.05:
        return 21
    days = gap / speed_day
    return int(clamp(round(days), 3, 60))

def signal(profit: float) -> Tuple[str, str]:
    if profit >= 10:
        return "BUY", "green"
    if profit >= 4:
        return "HOLD", "yellow"
    return "PASS", "red"

def skinport_item_link(item_page: str) -> str:
    return item_page or "https://skinport.com"

def escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))

# -----------------------------
# IMAGE: Placeholder (sempre funciona)
# -----------------------------
def placeholder_svg_data_uri(name: str) -> str:
    # pega “iniciais” do nome da arma/skin
    text = name.replace("StatTrak™ ", "")
    left = text.split("|")[0].strip() if "|" in text else text[:16].strip()
    initials = "".join([w[0].upper() for w in left.split() if w][:3]) or "CS2"

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1f2937"/>
      <stop offset="1" stop-color="#111827"/>
    </linearGradient>
  </defs>
  <rect width="160" height="120" rx="16" fill="url(#g)"/>
  <text x="14" y="70" font-family="Arial" font-size="34" fill="#e5e7eb" font-weight="700">{initials}</text>
  <text x="14" y="95" font-family="Arial" font-size="12" fill="#9ca3af">CS2 Skin Radar</text>
</svg>"""
    # data URI
    return "data:image/svg+xml;charset=utf-8," + quote(svg)

# -----------------------------
# IMAGE: REAL (opcional, com API key)
# -----------------------------
def load_image_cache() -> Dict[str, str]:
    cache = {}
    if not os.path.exists(IMAGE_CACHE_FILE):
        return cache
    with open(IMAGE_CACHE_FILE, "r", encoding="utf-8") as f:
        for line in f.read().splitlines()[1:]:
            try:
                k, v = line.split(",", 1)
                cache[k] = v
            except:
                pass
    return cache

def save_image_cache(cache: Dict[str, str]) -> None:
    with open(IMAGE_CACHE_FILE, "w", encoding="utf-8") as f:
        f.write("skin,image_url\n")
        for k, v in cache.items():
            f.write(f"{k},{v}\n")

def fetch_images_from_csgoskins(skins: List[str]) -> Dict[str, str]:
    """
    Busca imagens reais via csgoskins.gg (requer auth).
    Se não tiver API key, retorna {}.
    """
    if not CSGOSKINS_API_KEY:
        return {}

    # Esse endpoint é paginado e não dá pra buscar por lista de nomes de forma simples,
    # então aqui a estratégia é:
    # - NÃO varrer 17k itens (pesado)
    # - Manter cache e só tentar resolver imagens ausentes de vez em quando
    # Melhor prática: se você tiver key e quiser 100% imagens, eu adapto para indexar por páginas com limite.
    return {}

# -----------------------------
# API Skinport
# -----------------------------
def fetch_skinport_history() -> List[dict]:
    params = {"app_id": APP_ID, "currency": CURRENCY}
    headers = {
        # obrigatório pra endpoint history :contentReference[oaicite:3]{index=3}
        "Accept-Encoding": "br"
    }
    try:
        r = requests.get(SKINPORT_HISTORY_URL, params=params, headers=headers, timeout=50)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print("Erro API Skinport:", e)
        return []

# -----------------------------
# HISTORY CSV (preço atual + volume)
# -----------------------------
def append_history(ts: str, name: str, cur: float, vol30: int) -> None:
    exists = os.path.isfile(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "skin", "current_price", "volume_30d"])
        w.writerow([ts, name, cur, vol30])

# -----------------------------
# HTML (layout melhor + imagem + links)
# -----------------------------
def generate_html(rows: List[Dict]) -> None:
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    # cards rápidos no topo (Top 12)
    top_cards = rows[:12]
    cards_html = []
    for r in top_cards:
        cards_html.append(f"""
<a class="card" href="{r["item_page"]}" target="_blank" rel="noopener">
  <img class="thumb" src="{r["img"]}" alt="img"/>
  <div class="card-body">
    <div class="card-title">{escape_html(r["skin"])}</div>
    <div class="card-sub">
      <span class="pill">R$ {r["cur"]:.2f}</span>
      <span class="pill">Vol 30d: {r["vol30"]}</span>
      <span class="pill {r["color"]}">{r["sig"]} • {r["profit"]:.2f}%</span>
    </div>
  </div>
</a>
""")

    table_rows = []
    for r in rows:
        table_rows.append(f"""
<tr>
  <td class="skin-cell">
    <img class="mini" src="{r["img"]}" alt="img"/>
    <div class="skin-text">
      <div class="skin-name">{escape_html(r["skin"])}</div>
      <div class="skin-links">
        <a href="{r["item_page"]}" target="_blank" rel="noopener">Skinport</a>
        <span>•</span>
        <a href="{r["market_page"]}" target="_blank" rel="noopener">Market</a>
      </div>
    </div>
  </td>
  <td>R$ {r["cur"]:.2f}</td>
  <td>{r["vol30"]}</td>
  <td>R$ {r["avg7"]:.2f}</td>
  <td>R$ {r["avg30"]:.2f}</td>
  <td>{r["speed"]:.2f}%/dia</td>
  <td><b>{r["profit"]:.2f}%</b></td>
  <td>{r["hold_days"]}d</td>
  <td class="{r["color"]}">{r["sig"]}</td>
</tr>
""")

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CS2 Skin Radar</title>
<style>
  :root {{
    --bg: #0b0f14;
    --card: #0f1621;
    --card2: #0c131d;
    --text: #e5e7eb;
    --muted: #9ca3af;
    --line: #1f2a37;
    --accent: #60a5fa;
  }}
  body {{
    font-family: Arial, sans-serif;
    background: radial-gradient(1200px 600px at 10% 10%, #101827, var(--bg));
    color: var(--text);
    padding: 22px;
    margin: 0;
  }}
  .wrap {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{ margin: 0 0 6px 0; letter-spacing: .2px; }}
  .muted {{ color: var(--muted); margin: 0 0 18px 0; }}

  .bar {{
    display:flex; gap:12px; flex-wrap:wrap;
    margin: 14px 0 16px 0; align-items:center;
  }}
  input {{
    padding:10px 12px;
    border-radius:12px;
    border:1px solid var(--line);
    background:#070a0f;
    color:var(--text);
    outline:none;
    min-width: 260px;
  }}
  .chip {{
    padding:8px 10px;
    border:1px solid var(--line);
    border-radius:999px;
    background:#070a0f;
    color:#cbd5e1;
    font-size: 13px;
  }}

  .grid {{
    display:grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
    margin: 10px 0 18px 0;
  }}
  .card {{
    display:flex;
    gap: 12px;
    background: linear-gradient(180deg, var(--card), var(--card2));
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 12px;
    text-decoration:none;
    color: inherit;
    transition: transform .08s ease, border-color .08s ease;
  }}
  .card:hover {{
    transform: translateY(-2px);
    border-color: #2b3a4f;
  }}
  .thumb {{
    width: 72px;
    height: 54px;
    border-radius: 12px;
    border: 1px solid var(--line);
    object-fit: cover;
    flex: 0 0 auto;
    background:#05070b;
  }}
  .card-title {{
    font-weight: 700;
    font-size: 14px;
    line-height: 1.2;
    margin-bottom: 6px;
  }}
  .pill {{
    display:inline-block;
    padding: 6px 8px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background:#070a0f;
    font-size: 12px;
    color:#cbd5e1;
    margin-right: 6px;
    margin-bottom: 6px;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    border-radius: 16px;
    overflow: hidden;
    border: 1px solid var(--line);
    background: rgba(6, 9, 14, 0.7);
  }}
  th, td {{
    padding: 12px;
    text-align: left;
    border-bottom: 1px solid var(--line);
    vertical-align: middle;
  }}
  th {{
    background: rgba(15, 22, 33, 0.95);
    cursor: pointer;
    position: sticky;
    top: 0;
    z-index: 1;
    font-size: 13px;
    color: #cbd5e1;
  }}
  tr:hover {{ background: rgba(31, 42, 55, 0.35); }}

  .skin-cell {{
    display:flex;
    gap: 10px;
    align-items:center;
  }}
  .mini {{
    width: 48px;
    height: 36px;
    border-radius: 10px;
    border: 1px solid var(--line);
    object-fit: cover;
    background:#05070b;
    flex: 0 0 auto;
  }}
  .skin-name {{
    font-weight: 700;
    font-size: 13px;
    margin-bottom: 2px;
  }}
  .skin-links {{
    font-size: 12px;
    color: var(--muted);
    display:flex;
    gap:8px;
    align-items:center;
  }}
  .skin-links a {{
    color: var(--accent);
    text-decoration: none;
  }}
  .skin-links a:hover {{
    text-decoration: underline;
  }}

  .green {{ color:#00ff88; font-weight: 800; }}
  .yellow {{ color:#ffcc00; font-weight: 800; }}
  .red {{ color:#ff5555; font-weight: 800; }}

  .footer {{
    margin-top: 14px;
    color: var(--muted);
    font-size: 12px;
  }}
</style>
</head>
<body>
<div class="wrap">

  <h1>🚀 CS2 Skin Radar</h1>
  <p class="muted">Atualizado: {updated} • 500 skins (FT/MW/FN + StatTrak™) • base: Skinport sales/history</p>

  <div class="bar">
    <input id="q" placeholder="Buscar skin..." oninput="filterRows()"/>
    <span class="chip">Ordene clicando nos títulos</span>
    <span class="chip">% lucro = (média 30d - atual) / atual</span>
  </div>

  <div class="grid">
    {''.join(cards_html)}
  </div>

  <table id="t">
    <thead>
      <tr>
        <th onclick="sortTable(0)">Skin</th>
        <th onclick="sortTable(1)">Preço</th>
        <th onclick="sortTable(2)">Vol 30d</th>
        <th onclick="sortTable(3)">Média 7d</th>
        <th onclick="sortTable(4)">Média 30d</th>
        <th onclick="sortTable(5)">Veloc.</th>
        <th onclick="sortTable(6)">Lucro</th>
        <th onclick="sortTable(7)">Segurar</th>
        <th onclick="sortTable(8)">Sinal</th>
      </tr>
    </thead>
    <tbody>
      {''.join(table_rows)}
    </tbody>
  </table>

  <div class="footer">
    Nota: sinais e lucro são heurísticos (não é garantia). Volume 30d alto = mais liquidez.
  </div>

</div>

<script>
let sortDir = 1;
let lastCol = -1;

function parseCellText(td) {{
  // se for a coluna 0 (skin) pegar só texto do nome
  if (td.querySelector(".skin-name")) {{
    return td.querySelector(".skin-name").innerText.toLowerCase();
  }}
  const txt = td.innerText.trim();
  const cleaned = txt
    .replace("R$", "")
    .replace("%/dia", "")
    .replace("%", "")
    .replace("d", "")
    .replace(",", ".")
    .trim();
  const num = parseFloat(cleaned);
  if (!isNaN(num)) return num;
  return txt.toLowerCase();
}}

function sortTable(col) {{
  const table = document.getElementById("t");
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);

  if (col === lastCol) sortDir *= -1;
  else {{ sortDir = 1; lastCol = col; }}

  rows.sort((a, b) => {{
    const av = parseCellText(a.cells[col]);
    const bv = parseCellText(b.cells[col]);

    if (typeof av === "number" && typeof bv === "number") {{
      return (av - bv) * sortDir;
    }}
    if (av < bv) return -1 * sortDir;
    if (av > bv) return  1 * sortDir;
    return 0;
  }});

  rows.forEach(r => tbody.appendChild(r));
}}

function filterRows() {{
  const q = document.getElementById("q").value.toLowerCase();
  const table = document.getElementById("t");
  const tbody = table.tBodies[0];
  Array.from(tbody.rows).forEach(r => {{
    const name = r.querySelector(".skin-name").innerText.toLowerCase();
    r.style.display = name.includes(q) ? "" : "none";
  }});
}}
</script>
</body>
</html>
"""
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

# -----------------------------
# MAIN
# -----------------------------
def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    data = fetch_skinport_history()
    if not data:
        print("API vazia")
        return

    # filtra FT/MW/FN + volume 30d + preço válido
    filtered = []
    for it in data:
        name = it.get("market_hash_name")
        if not name:
            continue
        if not is_allowed_wear(name):
            continue

        w30 = get_window(it, "last_30_days")
        vol30 = int(safe_num(w30.get("volume"), 0))
        if vol30 < MIN_VOL_30D:
            continue

        cur = current_price(it)
        if not (MIN_CUR_PRICE <= cur <= MAX_CUR_PRICE):
            continue

        filtered.append(it)

    # separa stattrak / normal
    def vol30_of(x):
        return int(safe_num(get_window(x, "last_30_days").get("volume"), 0))

    st = [x for x in filtered if is_stattrak(x.get("market_hash_name", ""))]
    nm = [x for x in filtered if not is_stattrak(x.get("market_hash_name", ""))]

    st.sort(key=vol30_of, reverse=True)
    nm.sort(key=vol30_of, reverse=True)

    st_pick = st[:min(STAT_TRAK_QUOTA, TOTAL_ITEMS)]
    nm_pick = nm[:max(0, TOTAL_ITEMS - len(st_pick))]
    picked = st_pick + nm_pick

    rows = []
    for it in picked:
        name = it.get("market_hash_name", "")
        item_page = it.get("item_page", "")
        market_page = it.get("market_page", "")

        w7 = get_window(it, "last_7_days")
        w30 = get_window(it, "last_30_days")

        cur = current_price(it)
        avg7 = safe_num(w7.get("avg"), cur)
        avg30 = safe_num(w30.get("avg"), avg7)
        vol30v = int(safe_num(w30.get("volume"), 0))

        speed = speed_pct_per_day(it)
        profit = expected_profit_pct(cur, avg30)
        hold_days = hold_days_estimate(cur, avg30, speed)
        sig, color = signal(profit)

        append_history(ts, name, cur, vol30v)

        # imagem (placeholder sempre disponível)
        img = placeholder_svg_data_uri(name)

        rows.append({
            "skin": name,
            "cur": cur,
            "vol30": vol30v,
            "avg7": avg7,
            "avg30": avg30,
            "speed": speed,
            "profit": profit,
            "hold_days": hold_days,
            "sig": sig,
            "color": color,
            "img": img,
            "item_page": skinport_item_link(item_page),
            "market_page": market_page or skinport_item_link(item_page),
        })

    rows.sort(key=lambda r: (r["profit"], r["vol30"]), reverse=True)

    generate_html(rows)
    print(f"Ranking atualizado: {len(rows)} skins")

if __name__ == "__main__":
    main()
