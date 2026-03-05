import csv
import os
from datetime import datetime, timezone
from typing import List, Dict, Tuple

import pandas as pd
import requests

# -----------------------------
# CONFIG
# -----------------------------
API_URL = "https://api.skinport.com/v1/sales/history"  # ✅ certo
HISTORY_FILE = "skin_history.csv"
OUTPUT_HTML = "index.html"

APP_ID = 730
CURRENCY = "BRL"

# Quantidade alvo
TOTAL_ITEMS = 500
STAT_TRAK_QUOTA = 100  # quantidade de StatTrak™ dentro das 500 (ajuste se quiser)

# Condições permitidas
ALLOWED_WEAR = ("(Field-Tested)", "(Minimal Wear)", "(Factory New)")

# Filtros de liquidez
MIN_VOL_30D = 10       # volume mínimo em 30d (evita itens mortos)
MIN_CUR_PRICE = 1.0
MAX_CUR_PRICE = 10000.0

# Limita lucro estimado (pra não ficar irreal)
MAX_EXPECTED_PROFIT_PCT = 35.0

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
    """
    Estima "velocidade" diária (%/dia) usando amplitude 7d:
    speed ≈ ((max-min)/avg) / 7
    """
    w7 = get_window(item, "last_7_days")
    mn = safe_num(w7.get("min"), 0.0)
    mx = safe_num(w7.get("max"), 0.0)
    avg = safe_num(w7.get("avg"), 0.0)
    if avg <= 0 or mx <= 0 or mn <= 0 or mx < mn:
        return 0.0
    amp_pct = ((mx - mn) / avg) * 100.0
    return clamp(amp_pct / 7.0, 0.05, 15.0)  # evita 0 e outliers

def expected_profit_pct(cur: float, avg30: float) -> float:
    if cur <= 0 or avg30 <= 0:
        return 0.0
    raw = ((avg30 - cur) / cur) * 100.0
    raw = max(0.0, raw)  # só upside
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
    # simples e objetivo
    if profit >= 10:
        return "BUY", "green"
    if profit >= 4:
        return "HOLD", "yellow"
    return "PASS", "red"

# -----------------------------
# API
# -----------------------------
def fetch_history_all() -> List[dict]:
    params = {"app_id": APP_ID, "currency": CURRENCY}
    headers = {
        # ⚠️ obrigatório pra esse endpoint (Brotli)
        "Accept-Encoding": "br"
    }

    try:
        r = requests.get(API_URL, params=params, headers=headers, timeout=40)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print("Erro API:", e)
        return []

# -----------------------------
# HISTORY CSV (opcional, mas útil)
# -----------------------------
def append_history(ts: str, name: str, cur: float, vol30: int) -> None:
    exists = os.path.isfile(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "skin", "current_price", "volume_30d"])
        w.writerow([ts, name, cur, vol30])

# -----------------------------
# HTML
# -----------------------------
def generate_html(rows: List[Dict]) -> None:
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    body_rows = []
    for r in rows:
        body_rows.append(f"""
<tr>
  <td>{r["skin"]}</td>
  <td>R$ {r["cur"]:.2f}</td>
  <td>{r["vol30"]}</td>
  <td>R$ {r["avg7"]:.2f}</td>
  <td>R$ {r["avg30"]:.2f}</td>
  <td>{r["speed"]:.2f}%/dia</td>
  <td><b>{r["profit"]:.2f}%</b></td>
  <td>{r["hold_days"]} dias</td>
  <td class="{r["color"]}">{r["sig"]}</td>
</tr>
""")

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CS2 Skin Bot - Ranking</title>
<style>
  body {{
    font-family: Arial, sans-serif;
    background:#111;
    color:#fff;
    padding:24px;
  }}
  h1 {{ margin: 0 0 6px 0; }}
  .muted {{ color:#aaa; margin: 0 0 18px 0; }}
  .bar {{
    display:flex;
    gap:12px;
    flex-wrap:wrap;
    margin: 12px 0 16px 0;
    align-items:center;
  }}
  input {{
    padding:10px 12px;
    border-radius:10px;
    border:1px solid #333;
    background:#0c0c0c;
    color:#fff;
    outline:none;
    min-width: 260px;
  }}
  .chip {{
    padding:8px 10px;
    border:1px solid #333;
    border-radius:999px;
    background:#0c0c0c;
    color:#ddd;
    font-size: 13px;
  }}
  table {{
    width:100%;
    border-collapse: collapse;
    overflow:hidden;
    border-radius: 14px;
  }}
  th, td {{
    padding:12px;
    text-align:left;
    border-bottom:1px solid #222;
    vertical-align: middle;
  }}
  th {{
    background:#1b1b1b;
    cursor:pointer;
    position: sticky;
    top: 0;
    z-index: 1;
  }}
  tr:nth-child(even) {{ background:#151515; }}
  tr:hover {{ background:#1f1f1f; }}
  .green {{ color:#00ff88; font-weight:700; }}
  .yellow {{ color:#ffcc00; font-weight:700; }}
  .red {{ color:#ff5555; font-weight:700; }}
  .small {{ font-size: 12px; color:#aaa; }}
</style>
</head>
<body>

<h1>🔥 CS2 Skin Ranking</h1>
<p class="muted">Atualizado: {updated} • Fonte: Skinport sales/history • Apenas FT/MW/FN + mistura StatTrak™</p>

<div class="bar">
  <input id="q" placeholder="Buscar skin..." oninput="filterRows()"/>
  <span class="chip">Ordene clicando nos títulos</span>
  <span class="chip">% lucro = (média 30d - atual) / atual</span>
</div>

<table id="t">
  <thead>
    <tr>
      <th onclick="sortTable(0)">Skin</th>
      <th onclick="sortTable(1)">Preço atual</th>
      <th onclick="sortTable(2)">Vol 30d</th>
      <th onclick="sortTable(3)">Média 7d</th>
      <th onclick="sortTable(4)">Média 30d</th>
      <th onclick="sortTable(5)">Veloc.</th>
      <th onclick="sortTable(6)">Lucro est.</th>
      <th onclick="sortTable(7)">Segurar</th>
      <th onclick="sortTable(8)">Sinal</th>
    </tr>
  </thead>
  <tbody>
    {''.join(body_rows)}
  </tbody>
</table>

<p class="small" style="margin-top:14px;">
  Nota: isso é heurística (não é garantia). Volume 30d alto = mais liquidez.
</p>

<script>
let sortDir = 1;
let lastCol = -1;

function parseCellText(td) {{
  const txt = td.innerText.trim();
  const cleaned = txt
    .replace("R$", "")
    .replace("%/dia", "")
    .replace("%", "")
    .replace("dias", "")
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
  else {{
    sortDir = 1;
    lastCol = col;
  }}

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
    const name = r.cells[0].innerText.toLowerCase();
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

    data = fetch_history_all()
    if not data:
        print("API vazia")
        return

    # filtra FT/MW/FN e com volume 30d
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
    st = [x for x in filtered if is_stattrak(x.get("market_hash_name", ""))]
    nm = [x for x in filtered if not is_stattrak(x.get("market_hash_name", ""))]

    # ordena por volume 30d (liquidez)
    def vol30(x):
        return int(safe_num(get_window(x, "last_30_days").get("volume"), 0))

    st.sort(key=vol30, reverse=True)
    nm.sort(key=vol30, reverse=True)

    # pega quota stattrak + resto normal
    st_pick = st[:min(STAT_TRAK_QUOTA, TOTAL_ITEMS)]
    nm_pick = nm[:max(0, TOTAL_ITEMS - len(st_pick))]

    picked = st_pick + nm_pick

    rows = []
    for it in picked:
        name = it.get("market_hash_name", "")

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
            "color": color
        })

    # rank: maior lucro, depois volume
    rows.sort(key=lambda r: (r["profit"], r["vol30"]), reverse=True)

    generate_html(rows)
    print(f"Ranking atualizado: {len(rows)} skins")

if __name__ == "__main__":
    main()
