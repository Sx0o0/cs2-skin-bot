import csv
import os
import json
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

# Se quiser TODAS do history, deixe None.
# Se quiser limitar (pra não ficar gigante), coloque ex: 8000
MAX_ITEMS = None

# Condições: se quiser incluir tudo, use ALLOWED_WEAR = None
ALLOWED_WEAR = ("(Field-Tested)", "(Minimal Wear)", "(Factory New)")

# Filtros mínimos (pra slider fazer sentido). Pode botar 0 pra pegar tudo.
MIN_VOL_30D = 0
MIN_CUR_PRICE = 0.01
MAX_CUR_PRICE = 100000.0

MAX_EXPECTED_PROFIT_PCT = 35.0

# SteamApis Image map (sem key)
STEAMAPIS_IMAGE_ITEMS_URL = f"https://api.steamapis.com/image/items/{APP_ID}"
IMAGE_MAP_CACHE_FILE = "image_map_cache.json"
IMAGE_MAP_MAX_AGE_DAYS = 7


# -----------------------------
# WEAPON MAP (categorias)
# -----------------------------
PISTOLS = {
    "CZ75-Auto", "Desert Eagle", "Dual Berettas", "Five-SeveN", "Glock-18",
    "P2000", "P250", "R8 Revolver", "Tec-9", "USP-S"
}
RIFLES = {
    "AK-47", "AUG", "FAMAS", "Galil AR", "M4A1-S", "M4A4", "SG 553"
}
SMGS = {"MAC-10", "MP5-SD", "MP7", "MP9", "P90", "PP-Bizon", "UMP-45"}
HEAVY = {"MAG-7", "Nova", "Sawed-Off", "XM1014", "M249", "Negev"}
SNIPERS = {"AWP", "G3SG1", "SCAR-20", "SSG 08"}

# Alguns itens “não arma” podem aparecer no history (agents/stickers/etc)
# A gente separa como "Other".


# -----------------------------
# HELPERS
# -----------------------------
def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


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
    p24 = safe_num(get_window(item, "last_24_hours").get("avg"), 0.0)
    if p24 > 0:
        return p24
    return safe_num(get_window(item, "last_7_days").get("avg"), 0.0)


def speed_pct_per_day(item: dict) -> float:
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
    return int(clamp(round(gap / speed_day), 3, 60))


def signal(profit: float) -> Tuple[str, str]:
    if profit >= 10:
        return "BUY", "green"
    if profit >= 4:
        return "HOLD", "yellow"
    return "PASS", "red"


def escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def is_stattrak(name: str) -> bool:
    return name.startswith("StatTrak™ ")


def strip_stattrak(name: str) -> str:
    return name.replace("StatTrak™ ", "", 1)


def allowed_wear(name: str) -> bool:
    if ALLOWED_WEAR is None:
        return True
    return any(w in name for w in ALLOWED_WEAR)


def placeholder_svg_data_uri(name: str) -> str:
    text = strip_stattrak(name)
    left = text.split("|")[0].strip() if "|" in text else text[:16].strip()
    initials = "".join([w[0].upper() for w in left.split() if w][:3]) or "CS2"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#1f2937"/><stop offset="1" stop-color="#111827"/></linearGradient></defs>
  <rect width="160" height="120" rx="16" fill="url(#g)"/>
  <text x="14" y="70" font-family="Arial" font-size="34" fill="#e5e7eb" font-weight="700">{initials}</text>
  <text x="14" y="95" font-family="Arial" font-size="12" fill="#9ca3af">CS2 Skin Radar</text>
</svg>"""
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


# -----------------------------
# CATEGORY + WEAPON PARSE
# -----------------------------
def weapon_from_market_hash(name: str) -> str:
    base = strip_stattrak(name)
    left = base.split("|")[0].strip() if "|" in base else base.strip()

    # Itens especiais começam com ★ (facas/luvas)
    if left.startswith("★"):
        left2 = left.replace("★", "").strip()
        return left2

    return left


def category_from_weapon(weapon: str) -> str:
    # Gloves tem "Gloves" no nome
    if "Gloves" in weapon:
        return "Gloves"

    # Knives normalmente são ★ algo e weapon fica "Karambit", "Bayonet", etc.
    # Se weapon for conhecido por faca, pode não ter "Knife".
    # Heurística: se veio de ★ (o parse removeu o ★), a maioria cai aqui.
    # Como não temos o flag aqui, tratamos por nomes comuns e fallback por "Knife".
    if "Knife" in weapon or weapon in {
        "Karambit", "Bayonet", "M9 Bayonet", "Butterfly Knife", "Falchion Knife",
        "Bowie Knife", "Huntsman Knife", "Shadow Daggers", "Flip Knife", "Gut Knife",
        "Stiletto Knife", "Talon Knife", "Navaja Knife", "Ursus Knife",
        "Nomad Knife", "Skeleton Knife", "Survival Knife", "Paracord Knife",
        "Classic Knife", "Kukri Knife"
    }:
        return "Knives"

    if weapon in PISTOLS:
        return "Pistols"
    if weapon in RIFLES:
        return "Rifles"
    if weapon in SMGS:
        return "SMGs"
    if weapon in HEAVY:
        return "Heavy"
    if weapon in SNIPERS:
        return "Snipers"

    return "Other"


# -----------------------------
# IMAGES (SteamApis cache)
# -----------------------------
def load_image_map_cache() -> Tuple[Dict[str, str], datetime]:
    if not os.path.exists(IMAGE_MAP_CACHE_FILE):
        return {}, datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        with open(IMAGE_MAP_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        ts = payload.get("fetched_at", "")
        fetched_at = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else datetime.fromtimestamp(0, tz=timezone.utc)
        mapping = payload.get("mapping", {})
        if isinstance(mapping, dict):
            return mapping, fetched_at
    except:
        pass
    return {}, datetime.fromtimestamp(0, tz=timezone.utc)


def save_image_map_cache(mapping: Dict[str, str]) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mapping": mapping
    }
    with open(IMAGE_MAP_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def fetch_image_map_if_needed() -> Dict[str, str]:
    mapping, fetched_at = load_image_map_cache()
    age_days = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 86400.0
    if mapping and age_days < IMAGE_MAP_MAX_AGE_DAYS:
        return mapping

    try:
        r = requests.get(STEAMAPIS_IMAGE_ITEMS_URL, timeout=120)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data:
            save_image_map_cache(data)
            return data
    except Exception as e:
        print("Erro SteamApis images:", e)

    return mapping


def get_image_url(name: str, image_map: Dict[str, str]) -> str:
    url = image_map.get(name)
    if isinstance(url, str) and url.startswith("http"):
        return url
    return placeholder_svg_data_uri(name)


# -----------------------------
# API Skinport
# -----------------------------
def fetch_skinport_history() -> List[dict]:
    params = {"app_id": APP_ID, "currency": CURRENCY}
    headers = {
        "Accept-Encoding": "br",
        "User-Agent": "cs2-skin-radar/2.0 (+github-actions)"
    }
    try:
        r = requests.get(SKINPORT_HISTORY_URL, params=params, headers=headers, timeout=80)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print("Erro API Skinport:", e)
        return []


# -----------------------------
# HISTORY CSV
# -----------------------------
def append_history(ts: str, name: str, cur: float, vol30: int) -> None:
    exists = os.path.isfile(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "skin", "current_price", "volume_30d"])
        w.writerow([ts, name, cur, vol30])


# -----------------------------
# HTML generation (grouped sections + filters)
# -----------------------------
def generate_html(grouped: Dict[str, Dict[str, List[Dict]]],
                  weapon_list_sorted: List[str],
                  updated: str,
                  max_vol: int) -> None:

    # Dropdown options
    weapon_options = ['<option value="all">Todas as armas</option>']
    for w in weapon_list_sorted:
        weapon_options.append(f'<option value="{escape_html(w)}">{escape_html(w)}</option>')

    # Build sections
    category_order = ["Knives", "Gloves", "Rifles", "Snipers", "SMGs", "Pistols", "Heavy", "Other"]

    sections_html = []
    for cat in category_order:
        if cat not in grouped:
            continue

        weapons_map = grouped[cat]
        # ordenar armas por quantidade de skins
        weapon_items = sorted(weapons_map.items(), key=lambda kv: (-len(kv[1]), kv[0]))

        weapon_blocks = []
        for weapon, rows in weapon_items:
            # table rows
            trs = []
            for r in rows:
                # data attrs p/ filtro
                stattrak_attr = "1" if r["st"] else "0"
                trs.append(f"""
<tr class="row"
    data-weapon="{escape_html(r["weapon"])}"
    data-category="{escape_html(r["category"])}"
    data-stattrak="{stattrak_attr}"
    data-vol="{r["vol30"]}"
    data-name="{escape_html(r["skin_lc"])}">
  <td class="skin-cell">
    <img class="mini" src="{r["img"]}" alt="img" loading="lazy"/>
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
  <td>{r["profit"]:.2f}%</td>
  <td>{r["hold_days"]}d</td>
  <td class="{r["color"]}">{r["sig"]}</td>
</tr>
""")

            weapon_blocks.append(f"""
<details class="weapon" open>
  <summary>
    <span class="weapon-title">{escape_html(weapon)}</span>
    <span class="badge">{len(rows)} skins</span>
  </summary>
  <div class="table-wrap">
    <table class="t">
      <thead>
        <tr>
          <th>Skin</th>
          <th>Preço</th>
          <th>Vol 30d</th>
          <th>Lucro</th>
          <th>Segurar</th>
          <th>Sinal</th>
        </tr>
      </thead>
      <tbody>
        {''.join(trs)}
      </tbody>
    </table>
  </div>
</details>
""")

        sections_html.append(f"""
<details class="category" open data-category-block="{escape_html(cat)}">
  <summary>
    <span class="cat-title">{cat}</span>
    <span class="badge">{sum(len(v) for v in weapons_map.values())} skins</span>
  </summary>
  <div class="cat-body">
    {''.join(weapon_blocks)}
  </div>
</details>
""")

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CS2 Skin Radar</title>

<style>
:root {{
  --bg:#0b0f14;
  --panel:#0f1621;
  --panel2:#0c131d;
  --text:#e5e7eb;
  --muted:#9ca3af;
  --line:#1f2a37;
  --accent:#60a5fa;
}}

* {{ box-sizing:border-box; }}
body {{
  margin:0;
  font-family: Arial, sans-serif;
  color:var(--text);
  background: radial-gradient(1200px 600px at 10% 10%, #101827, var(--bg));
}}
.wrap {{
  max-width: 1320px;
  margin: 0 auto;
  padding: 18px;
}}

header {{
  display:flex;
  flex-wrap:wrap;
  gap: 12px;
  align-items:flex-end;
  justify-content:space-between;
  margin-bottom: 14px;
}}
h1 {{ margin:0; font-size: 24px; }}
.sub {{ margin: 4px 0 0 0; color: var(--muted); font-size: 13px; }}

.controls {{
  display:flex;
  gap:10px;
  flex-wrap:wrap;
  align-items:center;
}}
input[type="text"], select {{
  padding:10px 12px;
  border-radius:12px;
  border:1px solid var(--line);
  background:#070a0f;
  color:var(--text);
  outline:none;
}}
.toggle {{
  display:flex;
  gap:8px;
  align-items:center;
  padding:10px 12px;
  border-radius:12px;
  border:1px solid var(--line);
  background:#070a0f;
}}
.toggle input {{ transform: scale(1.1); }}

.slider {{
  display:flex;
  gap:10px;
  align-items:center;
  padding:10px 12px;
  border-radius:12px;
  border:1px solid var(--line);
  background:#070a0f;
}}
.slider input[type="range"] {{
  width: 180px;
}}

.badge {{
  font-size: 12px;
  color:#cbd5e1;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid var(--line);
  background:#070a0f;
}}

.category, .weapon {{
  border: 1px solid var(--line);
  border-radius: 16px;
  overflow:hidden;
  background: linear-gradient(180deg, var(--panel), var(--panel2));
  margin: 10px 0;
}}

.category summary, .weapon summary {{
  list-style:none;
  cursor:pointer;
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding: 14px 14px;
}}
.category summary::-webkit-details-marker,
.weapon summary::-webkit-details-marker {{ display:none; }}

.cat-title {{
  font-weight: 800;
  font-size: 16px;
}}
.weapon-title {{
  font-weight: 800;
  font-size: 14px;
}}
.cat-body {{
  padding: 0 12px 12px 12px;
}}

.weapon {{
  margin: 10px 0;
  background: rgba(8, 12, 18, 0.55);
}}
.weapon summary {{
  padding: 12px 12px;
}}

.table-wrap {{
  overflow:auto;
  border-top: 1px solid var(--line);
}}

table {{
  width:100%;
  border-collapse: collapse;
}}
th, td {{
  padding: 12px;
  text-align:left;
  border-bottom: 1px solid rgba(31, 42, 55, 0.7);
  vertical-align: middle;
}}
th {{
  position: sticky;
  top: 0;
  z-index: 1;
  background: rgba(15,22,33,.95);
  color: #cbd5e1;
  font-size: 13px;
}}
tr:hover {{ background: rgba(31,42,55,.35); }}

.skin-cell {{
  display:flex;
  gap:10px;
  align-items:center;
}}
.mini {{
  width: 54px;
  height: 40px;
  border-radius: 12px;
  border: 1px solid var(--line);
  object-fit: cover;
  background:#05070b;
  flex: 0 0 auto;
}}
.skin-name {{
  font-weight: 800;
  font-size: 13px;
  margin-bottom: 2px;
}}
.skin-links {{
  font-size: 12px;
  color: var(--muted);
  display:flex;
  gap: 8px;
  align-items:center;
}}
.skin-links a {{
  color: var(--accent);
  text-decoration:none;
}}
.skin-links a:hover {{ text-decoration:underline; }}

.green {{ color:#00ff88; font-weight:900; }}
.yellow {{ color:#ffcc00; font-weight:900; }}
.red {{ color:#ff5555; font-weight:900; }}

.footer {{
  margin-top: 10px;
  color: var(--muted);
  font-size: 12px;
}}
.kpi {{
  display:flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 10px 0 6px 0;
}}
.kpi .badge {{
  background: rgba(7,10,15,.7);
}}
</style>
</head>

<body>
<div class="wrap">
  <header>
    <div>
      <h1>🚀 CS2 Skin Radar</h1>
      <p class="sub">Atualizado: {updated} • Itens: site agrupado por categoria → arma • Imagens Steam CDN</p>
      <div class="kpi">
        <span class="badge" id="kpiVisible">Visíveis: 0</span>
        <span class="badge" id="kpiVol">Volume mínimo: 0</span>
      </div>
    </div>

    <div class="controls">
      <input id="q" type="text" placeholder="Buscar skin..." oninput="applyFilters()"/>

      <select id="weaponSelect" onchange="applyFilters()">
        {''.join(weapon_options)}
      </select>

      <div class="toggle">
        <input id="stToggle" type="checkbox" onchange="applyFilters()"/>
        <label for="stToggle">Só StatTrak™</label>
      </div>

      <div class="slider">
        <label for="volRange">Vol 30d</label>
        <input id="volRange" type="range" min="0" max="{max_vol}" value="0" step="1" oninput="applyFilters()"/>
        <span class="badge" id="volVal">0</span>
      </div>
    </div>
  </header>

  {''.join(sections_html)}

  <div class="footer">
    Nota: lucro/sinal é heurística (reversão à média 30d). Volume alto = mais liquidez.
  </div>
</div>

<script>
function applyFilters() {{
  const q = document.getElementById("q").value.toLowerCase().trim();
  const weapon = document.getElementById("weaponSelect").value;
  const onlyST = document.getElementById("stToggle").checked;
  const volMin = parseInt(document.getElementById("volRange").value || "0", 10);

  document.getElementById("volVal").innerText = volMin;
  document.getElementById("kpiVol").innerText = "Volume mínimo: " + volMin;

  const rows = Array.from(document.querySelectorAll("tr.row"));
  let visibleCount = 0;

  for (const r of rows) {{
    const name = r.getAttribute("data-name") || "";
    const rWeapon = r.getAttribute("data-weapon") || "";
    const st = r.getAttribute("data-stattrak") === "1";
    const vol = parseInt(r.getAttribute("data-vol") || "0", 10);

    let ok = true;

    if (q && !name.includes(q)) ok = false;
    if (weapon !== "all" && rWeapon !== weapon) ok = false;
    if (onlyST && !st) ok = false;
    if (vol < volMin) ok = false;

    r.style.display = ok ? "" : "none";
    if (ok) visibleCount++;
  }}

  // esconde weapons vazias
  const weaponBlocks = Array.from(document.querySelectorAll("details.weapon"));
  for (const wb of weaponBlocks) {{
    const anyVisible = Array.from(wb.querySelectorAll("tr.row")).some(x => x.style.display !== "none");
    wb.style.display = anyVisible ? "" : "none";
  }}

  // esconde categorias vazias
  const catBlocks = Array.from(document.querySelectorAll("details.category"));
  for (const cb of catBlocks) {{
    const anyVisible = Array.from(cb.querySelectorAll("details.weapon")).some(x => x.style.display !== "none");
    cb.style.display = anyVisible ? "" : "none";
  }}

  document.getElementById("kpiVisible").innerText = "Visíveis: " + visibleCount;
}}

window.addEventListener("load", () => {{
  applyFilters();
}});
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
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    image_map = fetch_image_map_if_needed()
    data = fetch_skinport_history()
    if not data:
        print("API vazia")
        return

    rows_all = []
    max_vol = 0

    for it in data:
        name = it.get("market_hash_name")
        if not name:
            continue
        if not allowed_wear(name):
            continue

        w30 = get_window(it, "last_30_days")
        vol30 = int(safe_num(w30.get("volume"), 0))
        if vol30 < MIN_VOL_30D:
            continue

        cur = current_price(it)
        if not (MIN_CUR_PRICE <= cur <= MAX_CUR_PRICE):
            continue

        w7 = get_window(it, "last_7_days")
        avg7 = safe_num(w7.get("avg"), cur)
        avg30 = safe_num(w30.get("avg"), avg7)

        profit = expected_profit_pct(cur, avg30)
        speed = speed_pct_per_day(it)
        hold_days = hold_days_estimate(cur, avg30, speed)
        sig, color = signal(profit)

        weapon = weapon_from_market_hash(name)
        cat = category_from_weapon(weapon)

        item_page = it.get("item_page") or "https://skinport.com"
        market_page = it.get("market_page") or item_page

        append_history(ts, name, cur, vol30)

        row = {
            "skin": name,
            "skin_lc": name.lower(),
            "cur": float(cur),
            "vol30": int(vol30),
            "profit": float(profit),
            "hold_days": int(hold_days),
            "sig": sig,
            "color": color,
            "weapon": weapon,
            "category": cat,
            "st": is_stattrak(name),
            "img": get_image_url(name, image_map),
            "item_page": item_page,
            "market_page": market_page,
        }
        rows_all.append(row)
        if vol30 > max_vol:
            max_vol = vol30

    # opcional: limitar total (se MAX_ITEMS não for None)
    # ordena por volume e pega top N (pra não estourar pages)
    rows_all.sort(key=lambda r: (r["vol30"], r["profit"]), reverse=True)
    if MAX_ITEMS is not None:
        rows_all = rows_all[:MAX_ITEMS]

    # lista de armas pro dropdown
    weapon_list_sorted = sorted({r["weapon"] for r in rows_all})

    # agrupar: categoria -> arma -> lista
    grouped: Dict[str, Dict[str, List[Dict]]] = {}
    for r in rows_all:
        grouped.setdefault(r["category"], {}).setdefault(r["weapon"], []).append(r)

    # ordena dentro de cada arma: maior lucro e volume primeiro
    for cat, weapons_map in grouped.items():
        for weapon, lst in weapons_map.items():
            lst.sort(key=lambda r: (r["profit"], r["vol30"]), reverse=True)

    generate_html(grouped, weapon_list_sorted, updated, max_vol)
    print(f"Ranking atualizado: {len(rows_all)} itens | armas: {len(weapon_list_sorted)} | max vol: {max_vol}")


if __name__ == "__main__":
    main()
