import csv
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from urllib.parse import quote

import requests
from nacl.signing import SigningKey

# =========================================================
# CONFIG
# =========================================================
APP_ID = 730
CURRENCY = "USD"

SKINPORT_HISTORY_URL = "https://api.skinport.com/v1/sales/history"
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"
DMARKET_ROOT = "https://api.dmarket.com"

STEAMAPIS_IMAGE_ITEMS_URL = f"https://api.steamapis.com/image/items/{APP_ID}"

OUTPUT_HTML = "index.html"
HISTORY_FILE = "skin_history.csv"
IMAGE_MAP_CACHE_FILE = "image_map_cache.json"

# None = tenta listar tudo que vier da Skinport.
# Se o site ficar pesado, troque por algo tipo 3000 ou 5000.
MAX_ITEMS = None

# Quantas skins mais líquidas vão ter comparação cruzada com CSFloat/DMarket.
# Pode subir, mas o workflow fica mais lento.
CROSS_MARKET_LIMIT = 150

# Se quiser todas as wears, deixe None.
ALLOWED_WEAR = None
# Exemplo se quiser limitar:
# ALLOWED_WEAR = ("(Factory New)", "(Minimal Wear)", "(Field-Tested)", "(Well-Worn)", "(Battle-Scarred)")

MIN_VOLUME_30D = 0
MIN_PRICE = 0.01
MAX_PRICE = 100000.0

MAX_EXPECTED_PROFIT_PCT = 35.0
IMAGE_MAP_MAX_AGE_DAYS = 7

# =========================================================
# WEAPON / CATEGORY MAP
# =========================================================
PISTOLS = {
    "CZ75-Auto", "Desert Eagle", "Dual Berettas", "Five-SeveN", "Glock-18",
    "P2000", "P250", "R8 Revolver", "Tec-9", "USP-S", "Zeus x27"
}
RIFLES = {
    "AK-47", "AUG", "FAMAS", "Galil AR", "M4A1-S", "M4A4", "SG 553"
}
SMGS = {"MAC-10", "MP5-SD", "MP7", "MP9", "P90", "PP-Bizon", "UMP-45"}
HEAVY = {"MAG-7", "Nova", "Sawed-Off", "XM1014", "M249", "Negev"}
SNIPERS = {"AWP", "G3SG1", "SCAR-20", "SSG 08"}

KNOWN_KNIVES = {
    "Karambit", "Bayonet", "M9 Bayonet", "Butterfly Knife", "Falchion Knife",
    "Bowie Knife", "Huntsman Knife", "Shadow Daggers", "Flip Knife", "Gut Knife",
    "Stiletto Knife", "Talon Knife", "Navaja Knife", "Ursus Knife",
    "Nomad Knife", "Skeleton Knife", "Survival Knife", "Paracord Knife",
    "Classic Knife", "Kukri Knife", "Canis Knife", "Cord Knife"
}

# =========================================================
# HELPERS
# =========================================================
def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def safe_float(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def safe_int(v, default=0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def allowed_wear(name: str) -> bool:
    if ALLOWED_WEAR is None:
        return True
    return any(w in name for w in ALLOWED_WEAR)


def is_stattrak(name: str) -> bool:
    return name.startswith("StatTrak™ ")


def strip_stattrak(name: str) -> str:
    return name.replace("StatTrak™ ", "", 1)


def get_window(item: dict, key: str) -> dict:
    w = item.get(key)
    return w if isinstance(w, dict) else {}


def current_price_from_skinport(item: dict) -> float:
    last24 = get_window(item, "last_24_hours")
    last7 = get_window(item, "last_7_days")
    p24 = safe_float(last24.get("avg"), 0.0)
    if p24 > 0:
        return p24
    return safe_float(last7.get("avg"), 0.0)


def avg30_from_skinport(item: dict) -> float:
    last30 = get_window(item, "last_30_days")
    last7 = get_window(item, "last_7_days")
    p30 = safe_float(last30.get("avg"), 0.0)
    if p30 > 0:
        return p30
    return safe_float(last7.get("avg"), 0.0)


def volume30_from_skinport(item: dict) -> int:
    last30 = get_window(item, "last_30_days")
    return safe_int(last30.get("volume"), 0)


def speed_pct_per_day_from_skinport(item: dict) -> float:
    w7 = get_window(item, "last_7_days")
    mn = safe_float(w7.get("min"), 0.0)
    mx = safe_float(w7.get("max"), 0.0)
    avg = safe_float(w7.get("avg"), 0.0)
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


def score_signal(profit_pct: float) -> Tuple[str, str]:
    if profit_pct >= 10:
        return "BUY", "green"
    if profit_pct >= 4:
        return "HOLD", "yellow"
    return "PASS", "red"


def weapon_from_market_hash(name: str) -> str:
    base = strip_stattrak(name)
    left = base.split("|")[0].strip() if "|" in base else base.strip()
    if left.startswith("★"):
        return left.replace("★", "").strip()
    return left


def category_from_weapon(weapon: str) -> str:
    if "Gloves" in weapon:
        return "Gloves"
    if "Knife" in weapon or weapon in KNOWN_KNIVES:
        return "Knives"
    if weapon in RIFLES:
        return "Rifles"
    if weapon in SNIPERS:
        return "Snipers"
    if weapon in SMGS:
        return "SMGs"
    if weapon in PISTOLS:
        return "Pistols"
    if weapon in HEAVY:
        return "Heavy"
    return "Other"


def chunks(items: List[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def placeholder_svg_data_uri(name: str) -> str:
    text = strip_stattrak(name)
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
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


# =========================================================
# IMAGE MAP CACHE
# =========================================================
def load_image_map_cache() -> Tuple[Dict[str, str], datetime]:
    if not os.path.exists(IMAGE_MAP_CACHE_FILE):
        return {}, datetime.fromtimestamp(0, tz=timezone.utc)

    try:
        with open(IMAGE_MAP_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        mapping = payload.get("mapping", {})
        fetched_at_raw = payload.get("fetched_at", "")
        fetched_at = (
            datetime.fromisoformat(fetched_at_raw.replace("Z", "+00:00"))
            if fetched_at_raw else
            datetime.fromtimestamp(0, tz=timezone.utc)
        )
        if isinstance(mapping, dict):
            return mapping, fetched_at
    except Exception:
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
        r = requests.get(STEAMAPIS_IMAGE_ITEMS_URL, timeout=90)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data:
            save_image_map_cache(data)
            return data
    except Exception as e:
        print("SteamApis image map falhou:", e)

    return mapping


def image_for_skin(name: str, image_map: Dict[str, str]) -> str:
    url = image_map.get(name)
    if isinstance(url, str) and url.startswith("http"):
        return url
    return placeholder_svg_data_uri(name)


# =========================================================
# SKINPORT
# =========================================================
def fetch_skinport_history_all() -> List[dict]:
    params = {"app_id": APP_ID, "currency": CURRENCY}
    headers = {
        "Accept-Encoding": "br",
        "User-Agent": "cs2-skin-radar/3.0"
    }
    r = requests.get(SKINPORT_HISTORY_URL, params=params, headers=headers, timeout=80)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return []
    return data


# =========================================================
# CSFLOAT
# =========================================================
def fetch_csfloat_lowest_for_titles(titles: List[str], pause_seconds: float = 0.12) -> Dict[str, dict]:
    session = requests.Session()
    out: Dict[str, dict] = {}

    for title in titles:
        params = {
            "market_hash_name": title,
            "limit": 1,
            "sort_by": "lowest_price",
            "type": "buy_now",
        }
        try:
            r = session.get(CSFLOAT_LISTINGS_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            if isinstance(data, list) and data:
                listing = data[0]
                price_cents = listing.get("price")
                item = listing.get("item") or {}
                market_hash_name = item.get("market_hash_name") or title

                if price_cents is not None:
                    out[market_hash_name] = {
                        "lowest_price": round(float(price_cents) / 100.0, 2),
                        "listing_id": listing.get("id"),
                        "url": f"https://csfloat.com/search?market_hash_name={quote(market_hash_name)}",
                    }
        except Exception as e:
            print(f"CSFloat falhou em {title}: {e}")

        time.sleep(pause_seconds)

    return out


# =========================================================
# DMARKET
# =========================================================
def _dmarket_headers(method: str, path: str, body: str = "") -> dict | None:
    api_key = os.getenv("DMARKET_API_KEY", "").strip().lower()
    secret_key = os.getenv("DMARKET_API_SECRET", "").strip().lower()

    if not api_key or not secret_key:
        return None

    try:
        signing_key = SigningKey(bytes.fromhex(secret_key))
    except Exception as e:
        print("DMARKET_API_SECRET inválido:", e)
        return None

    nonce = str(int(time.time()))
    string_to_sign = method + path + body + nonce
    signature = signing_key.sign(string_to_sign.encode("utf-8")).signature.hex()

    return {
        "X-Api-Key": api_key,
        "X-Sign-Date": nonce,
        "X-Request-Sign": f"dmar ed25519 {signature}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "cs2-skin-radar/3.0",
    }


def fetch_dmarket_aggregated_prices(titles: List[str]) -> Dict[str, dict]:
    if not titles:
        return {}

    path = "/marketplace-api/v1/aggregated-prices"
    out: Dict[str, dict] = {}

    for batch in chunks(titles, 50):
        response_json = None

        for game_id in ("csgo", "a8db"):
            payload = {
                "limit": str(len(batch)),
                "filter": {
                    "game": game_id,
                    "titles": batch
                }
            }
            body = json.dumps(payload, separators=(",", ":"))
            headers = _dmarket_headers("POST", path, body)

            if headers is None:
                return {}

            try:
                r = requests.post(DMARKET_ROOT + path, data=body, headers=headers, timeout=40)
                if r.status_code == 200:
                    response_json = r.json()
                    break
                else:
                    print(f"DMarket game={game_id} respondeu {r.status_code}: {r.text[:160]}")
            except Exception as e:
                print(f"DMarket falhou no batch {batch[:2]}...:", e)

        if not response_json:
            continue

        for item in response_json.get("aggregatedPrices", []):
            title = item.get("title")
            if not title:
                continue

            offer_best = item.get("offerBestPrice") or {}
            order_best = item.get("orderBestPrice") or {}

            out[title] = {
                "offer_best_price": round(safe_float(offer_best.get("Amount"), 0.0), 2),
                "order_best_price": round(safe_float(order_best.get("Amount"), 0.0), 2),
                "offer_count": safe_int(item.get("offerCount"), 0),
                "order_count": safe_int(item.get("orderCount"), 0),
                "currency": offer_best.get("Currency") or order_best.get("Currency") or "USD",
            }

    return out


# =========================================================
# HISTORY CSV
# =========================================================
def append_history(ts: str, name: str, current_price: float, volume30: int) -> None:
    file_exists = os.path.isfile(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp", "skin", "current_price_usd", "volume30"])
        w.writerow([ts, name, current_price, volume30])


# =========================================================
# MERGE
# =========================================================
def best_buy_sell(sp_price: float, cf_price: float, dm_offer: float, dm_order: float) -> Tuple[str, float, str, float, float]:
    buy_candidates = []
    sell_candidates = []

    if sp_price > 0:
        buy_candidates.append(("Skinport", sp_price))
        sell_candidates.append(("Skinport", sp_price))

    if cf_price > 0:
        buy_candidates.append(("CSFloat", cf_price))
        sell_candidates.append(("CSFloat", cf_price))

    if dm_offer > 0:
        buy_candidates.append(("DMarket", dm_offer))

    if dm_order > 0:
        sell_candidates.append(("DMarket", dm_order))

    best_buy_site, best_buy_price = "-", 0.0
    best_sell_site, best_sell_price = "-", 0.0

    if buy_candidates:
        best_buy_site, best_buy_price = min(buy_candidates, key=lambda x: x[1])

    if sell_candidates:
        best_sell_site, best_sell_price = max(sell_candidates, key=lambda x: x[1])

    spread_pct = 0.0
    if best_buy_price > 0 and best_sell_price > 0:
        spread_pct = ((best_sell_price - best_buy_price) / best_buy_price) * 100.0

    return best_buy_site, best_buy_price, best_sell_site, best_sell_price, round(spread_pct, 2)


# =========================================================
# HTML
# =========================================================
def generate_html(grouped: Dict[str, Dict[str, List[dict]]], weapons: List[str], updated: str, max_volume: int, top_cards: List[dict]) -> None:
    weapon_options = ['<option value="all">Todas as armas</option>']
    for weapon in weapons:
        weapon_options.append(f'<option value="{escape_html(weapon)}">{escape_html(weapon)}</option>')

    category_options = ['<option value="all">Todas as categorias</option>']
    for cat in ["Knives", "Gloves", "Rifles", "Snipers", "SMGs", "Pistols", "Heavy", "Other"]:
        if cat in grouped:
            category_options.append(f'<option value="{cat}">{cat}</option>')

    card_html = []
    for r in top_cards:
        card_html.append(f"""
<a class="card" href="{r["item_page"]}" target="_blank" rel="noopener">
  <img class="thumb" src="{r["img"]}" alt="img" loading="lazy"/>
  <div class="card-body">
    <div class="card-title">{escape_html(r["skin"])}</div>
    <div class="card-pills">
      <span class="pill">Spread {r["spread_pct"]:.2f}%</span>
      <span class="pill">Buy {escape_html(r["best_buy_site"])} ${r["best_buy_price"]:.2f}</span>
      <span class="pill">Sell {escape_html(r["best_sell_site"])} ${r["best_sell_price"]:.2f}</span>
    </div>
  </div>
</a>
""")

    category_order = ["Knives", "Gloves", "Rifles", "Snipers", "SMGs", "Pistols", "Heavy", "Other"]
    sections_html = []

    for cat in category_order:
        if cat not in grouped:
            continue

        weapons_map = grouped[cat]
        weapon_items = sorted(weapons_map.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))

        weapon_blocks = []
        for weapon, rows in weapon_items:
            trs = []
            for r in rows:
                trs.append(f"""
<tr class="row"
    data-name="{escape_html(r["skin"].lower())}"
    data-weapon="{escape_html(r["weapon"])}"
    data-category="{escape_html(r["category"])}"
    data-stattrak="{1 if r["st"] else 0}"
    data-vol="{r["vol30"]}">
  <td class="skin-cell">
    <img class="mini" src="{r["img"]}" alt="img" loading="lazy"/>
    <div class="skin-text">
      <div class="skin-name">{escape_html(r["skin"])}</div>
      <div class="skin-links">
        <a href="{r["item_page"]}" target="_blank" rel="noopener">Skinport</a>
        <span>•</span>
        <a href="{r["csfloat_url"]}" target="_blank" rel="noopener">CSFloat</a>
      </div>
    </div>
  </td>
  <td>${r["skinport_price"]:.2f}</td>
  <td>{r["vol30"]}</td>
  <td>${r["csfloat_price"]:.2f}</td>
  <td>${r["dmarket_offer"]:.2f}</td>
  <td>${r["dmarket_order"]:.2f}</td>
  <td>{escape_html(r["best_buy_site"])} ${r["best_buy_price"]:.2f}</td>
  <td>{escape_html(r["best_sell_site"])} ${r["best_sell_price"]:.2f}</td>
  <td>{r["spread_pct"]:.2f}%</td>
  <td>{r["hold_days"]}d</td>
  <td class="{r["signal_color"]}">{r["signal"]}</td>
</tr>
""")

            weapon_blocks.append(f"""
<details class="weapon" open>
  <summary>
    <span class="weapon-title">{escape_html(weapon)}</span>
    <span class="badge">{len(rows)} skins</span>
  </summary>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Skin</th>
          <th>Skinport</th>
          <th>Vol 30d</th>
          <th>CSFloat</th>
          <th>DMarket Sell</th>
          <th>DMarket Buy</th>
          <th>Best Buy</th>
          <th>Best Sell</th>
          <th>Spread</th>
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

        total_cat_items = sum(len(v) for v in weapons_map.values())
        sections_html.append(f"""
<details class="category" open>
  <summary>
    <span class="cat-title">{cat}</span>
    <span class="badge">{total_cat_items} skins</span>
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
  --line:#1f2a37;
  --text:#e5e7eb;
  --muted:#9ca3af;
  --accent:#60a5fa;
}}

* {{ box-sizing:border-box; }}

body {{
  margin:0;
  font-family: Arial, sans-serif;
  background: radial-gradient(1200px 600px at 10% 10%, #101827, var(--bg));
  color:var(--text);
}}

.wrap {{
  max-width: 1400px;
  margin: 0 auto;
  padding: 18px;
}}

header {{
  display:flex;
  gap:16px;
  justify-content:space-between;
  align-items:flex-end;
  flex-wrap:wrap;
  margin-bottom:14px;
}}

h1 {{
  margin:0;
  font-size: 26px;
}}

.sub {{
  margin:6px 0 0 0;
  font-size:13px;
  color:var(--muted);
}}

.kpis {{
  display:flex;
  gap:10px;
  flex-wrap:wrap;
  margin-top:10px;
}}

.badge {{
  display:inline-block;
  padding:7px 10px;
  border-radius:999px;
  border:1px solid var(--line);
  background:#070a0f;
  color:#d1d5db;
  font-size:12px;
}}

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

.toggle, .slider {{
  display:flex;
  gap:8px;
  align-items:center;
  padding:10px 12px;
  border-radius:12px;
  border:1px solid var(--line);
  background:#070a0f;
}}

.slider input[type="range"] {{
  width: 180px;
}}

.cards {{
  display:grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap:12px;
  margin: 12px 0 18px 0;
}}

.card {{
  display:flex;
  gap:12px;
  text-decoration:none;
  color:inherit;
  background: linear-gradient(180deg, var(--panel), var(--panel2));
  border:1px solid var(--line);
  border-radius:18px;
  padding:12px;
  transition: transform .08s ease, border-color .08s ease;
}}

.card:hover {{
  transform:translateY(-2px);
  border-color:#2b3a4f;
}}

.thumb {{
  width:78px;
  height:58px;
  border-radius:12px;
  object-fit:cover;
  border:1px solid var(--line);
  background:#05070b;
  flex:0 0 auto;
}}

.card-title {{
  font-weight:800;
  font-size:14px;
  line-height:1.25;
  margin-bottom:6px;
}}

.card-pills .pill {{
  display:inline-block;
  margin-right:6px;
  margin-bottom:6px;
  padding:6px 8px;
  border-radius:999px;
  border:1px solid var(--line);
  background:#070a0f;
  color:#cbd5e1;
  font-size:12px;
}}

.category, .weapon {{
  border:1px solid var(--line);
  border-radius:18px;
  overflow:hidden;
  background: linear-gradient(180deg, var(--panel), var(--panel2));
  margin: 10px 0;
}}

.category summary, .weapon summary {{
  list-style:none;
  display:flex;
  align-items:center;
  justify-content:space-between;
  cursor:pointer;
  padding:14px;
}}

.category summary::-webkit-details-marker,
.weapon summary::-webkit-details-marker {{
  display:none;
}}

.cat-title {{
  font-size:17px;
  font-weight:900;
}}

.weapon-title {{
  font-size:14px;
  font-weight:800;
}}

.cat-body {{
  padding:0 12px 12px 12px;
}}

.weapon {{
  margin:10px 0;
  background: rgba(8,12,18,.5);
}}

.table-wrap {{
  overflow:auto;
  border-top:1px solid var(--line);
}}

table {{
  width:100%;
  border-collapse:collapse;
}}

th, td {{
  padding:12px;
  text-align:left;
  border-bottom:1px solid rgba(31,42,55,.7);
  vertical-align:middle;
  white-space:nowrap;
}}

th {{
  position:sticky;
  top:0;
  z-index:1;
  background:rgba(15,22,33,.95);
  color:#cbd5e1;
  font-size:13px;
}}

tr:hover {{
  background: rgba(31,42,55,.35);
}}

.skin-cell {{
  display:flex;
  gap:10px;
  align-items:center;
  min-width:320px;
}}

.mini {{
  width:54px;
  height:40px;
  border-radius:12px;
  object-fit:cover;
  border:1px solid var(--line);
  background:#05070b;
  flex:0 0 auto;
}}

.skin-name {{
  font-size:13px;
  font-weight:800;
  margin-bottom:2px;
  white-space:normal;
}}

.skin-links {{
  display:flex;
  gap:8px;
  align-items:center;
  font-size:12px;
  color:var(--muted);
}}

.skin-links a {{
  color:var(--accent);
  text-decoration:none;
}}

.skin-links a:hover {{
  text-decoration:underline;
}}

.green {{ color:#00ff88; font-weight:900; }}
.yellow {{ color:#ffcc00; font-weight:900; }}
.red {{ color:#ff5555; font-weight:900; }}

.footer {{
  margin-top: 10px;
  color:var(--muted);
  font-size:12px;
}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>🚀 CS2 Skin Radar</h1>
      <p class="sub">Atualizado: {updated} • Base Skinport + comparação CSFloat + DMarket</p>
      <div class="kpis">
        <span class="badge" id="kpiVisible">Visíveis: 0</span>
        <span class="badge" id="kpiVolume">Volume mínimo: 0</span>
      </div>
    </div>

    <div class="controls">
      <input id="q" type="text" placeholder="Buscar skin..." oninput="applyFilters()"/>
      <select id="categorySelect" onchange="applyFilters()">
        {''.join(category_options)}
      </select>
      <select id="weaponSelect" onchange="applyFilters()">
        {''.join(weapon_options)}
      </select>
      <div class="toggle">
        <input id="stToggle" type="checkbox" onchange="applyFilters()"/>
        <label for="stToggle">Só StatTrak™</label>
      </div>
      <div class="slider">
        <label for="volRange">Vol 30d</label>
        <input id="volRange" type="range" min="0" max="{max_volume}" value="0" step="1" oninput="applyFilters()"/>
        <span class="badge" id="volValue">0</span>
      </div>
    </div>
  </header>

  <div class="cards">
    {''.join(card_html)}
  </div>

  {''.join(sections_html)}

  <div class="footer">
    Spread = melhor preço de venda / melhor preço de compra entre os marketplaces comparados.
  </div>
</div>

<script>
function applyFilters() {{
  const q = document.getElementById("q").value.toLowerCase().trim();
  const category = document.getElementById("categorySelect").value;
  const weapon = document.getElementById("weaponSelect").value;
  const onlyST = document.getElementById("stToggle").checked;
  const volMin = parseInt(document.getElementById("volRange").value || "0", 10);

  document.getElementById("volValue").innerText = volMin;
  document.getElementById("kpiVolume").innerText = "Volume mínimo: " + volMin;

  const rows = Array.from(document.querySelectorAll("tr.row"));
  let visibleCount = 0;

  for (const r of rows) {{
    const name = r.dataset.name || "";
    const rowWeapon = r.dataset.weapon || "";
    const rowCategory = r.dataset.category || "";
    const rowST = r.dataset.stattrak === "1";
    const rowVol = parseInt(r.dataset.vol || "0", 10);

    let ok = true;

    if (q && !name.includes(q)) ok = false;
    if (category !== "all" && rowCategory !== category) ok = false;
    if (weapon !== "all" && rowWeapon !== weapon) ok = false;
    if (onlyST && !rowST) ok = false;
    if (rowVol < volMin) ok = false;

    r.style.display = ok ? "" : "none";
    if (ok) visibleCount++;
  }}

  const weaponBlocks = Array.from(document.querySelectorAll("details.weapon"));
  for (const wb of weaponBlocks) {{
    const anyVisible = Array.from(wb.querySelectorAll("tr.row")).some(x => x.style.display !== "none");
    wb.style.display = anyVisible ? "" : "none";
  }}

  const categoryBlocks = Array.from(document.querySelectorAll("details.category"));
  for (const cb of categoryBlocks) {{
    const anyVisible = Array.from(cb.querySelectorAll("details.weapon")).some(x => x.style.display !== "none");
    cb.style.display = anyVisible ? "" : "none";
  }}

  document.getElementById("kpiVisible").innerText = "Visíveis: " + visibleCount;
}}

window.addEventListener("load", applyFilters);
</script>
</body>
</html>
"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)


# =========================================================
# MAIN
# =========================================================
def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    image_map = fetch_image_map_if_needed()

    # Base completa: Skinport
    try:
        skinport_raw = fetch_skinport_history_all()
    except Exception as e:
        print("Skinport falhou:", e)
        return

    rows_all: List[dict] = []
    max_volume = 0

    for item in skinport_raw:
        name = item.get("market_hash_name")
        if not name:
            continue
        if not allowed_wear(name):
            continue

        current_price = current_price_from_skinport(item)
        avg30 = avg30_from_skinport(item)
        volume30 = volume30_from_skinport(item)

        if current_price < MIN_PRICE or current_price > MAX_PRICE:
            continue
        if volume30 < MIN_VOLUME_30D:
            continue

        profit_pct = expected_profit_pct(current_price, avg30)
        hold_days = hold_days_estimate(current_price, avg30, speed_pct_per_day_from_skinport(item))
        signal, signal_color = score_signal(profit_pct)

        weapon = weapon_from_market_hash(name)
        category = category_from_weapon(weapon)

        item_page = item.get("item_page") or "https://skinport.com"
        market_page = item.get("market_page") or item_page

        append_history(ts, name, current_price, volume30)

        row = {
            "skin": name,
            "weapon": weapon,
            "category": category,
            "st": is_stattrak(name),
            "img": image_for_skin(name, image_map),
            "item_page": item_page,
            "market_page": market_page,
            "skinport_price": round(current_price, 2),
            "avg30": round(avg30, 2),
            "vol30": volume30,
            "profit_pct": profit_pct,
            "hold_days": hold_days,
            "signal": signal,
            "signal_color": signal_color,
            "csfloat_price": 0.0,
            "csfloat_url": f"https://csfloat.com/search?market_hash_name={quote(name)}",
            "dmarket_offer": 0.0,
            "dmarket_order": 0.0,
            "best_buy_site": "-",
            "best_buy_price": 0.0,
            "best_sell_site": "-",
            "best_sell_price": 0.0,
            "spread_pct": 0.0,
        }

        rows_all.append(row)
        if volume30 > max_volume:
            max_volume = volume30

    rows_all.sort(key=lambda r: (r["vol30"], r["profit_pct"]), reverse=True)

    if MAX_ITEMS is not None:
        rows_all = rows_all[:MAX_ITEMS]

    compare_titles = [r["skin"] for r in rows_all[:CROSS_MARKET_LIMIT]]

    # CSFloat
    csfloat_map = fetch_csfloat_lowest_for_titles(compare_titles)

    # DMarket (opcional, se houver secrets)
    dmarket_map = fetch_dmarket_aggregated_prices(compare_titles)

    # Merge cross-market
    for row in rows_all:
        title = row["skin"]

        cf = csfloat_map.get(title, {})
        dm = dmarket_map.get(title, {})

        row["csfloat_price"] = round(safe_float(cf.get("lowest_price"), 0.0), 2)
        row["dmarket_offer"] = round(safe_float(dm.get("offer_best_price"), 0.0), 2)
        row["dmarket_order"] = round(safe_float(dm.get("order_best_price"), 0.0), 2)

        best_buy_site, best_buy_price, best_sell_site, best_sell_price, spread_pct = best_buy_sell(
            row["skinport_price"],
            row["csfloat_price"],
            row["dmarket_offer"],
            row["dmarket_order"]
        )

        row["best_buy_site"] = best_buy_site
        row["best_buy_price"] = round(best_buy_price, 2)
        row["best_sell_site"] = best_sell_site
        row["best_sell_price"] = round(best_sell_price, 2)
        row["spread_pct"] = spread_pct

    # Agrupar por categoria > arma
    grouped: Dict[str, Dict[str, List[dict]]] = {}
    for row in rows_all:
        grouped.setdefault(row["category"], {}).setdefault(row["weapon"], []).append(row)

    # Ordenar dentro de cada arma
    for _, weapons_map in grouped.items():
        for _, items in weapons_map.items():
            items.sort(key=lambda r: (r["spread_pct"], r["vol30"], r["profit_pct"]), reverse=True)

    # Dropdown de armas
    weapons = sorted({r["weapon"] for r in rows_all}, key=lambda x: x.lower())

    # Top cards = top spreads úteis
    top_cards = sorted(
        [r for r in rows_all if r["spread_pct"] > 0],
        key=lambda r: (r["spread_pct"], r["vol30"]),
        reverse=True
    )[:12]

    generate_html(grouped, weapons, updated, max_volume, top_cards)

    print(f"Skinport base: {len(rows_all)}")
    print(f"CSFloat comparadas: {len(csfloat_map)}")
    print(f"DMarket comparadas: {len(dmarket_map)}")
    print("Ranking atualizado com sucesso.")


if __name__ == "__main__":
    main()
