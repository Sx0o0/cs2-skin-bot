import json
import os
import time
from typing import Dict, List, Iterable

import requests
from nacl.bindings import crypto_sign


SKINPORT_HISTORY_URL = "https://api.skinport.com/v1/sales/history"
CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"
DMARKET_ROOT = "https://api.dmarket.com"

APP_ID = 730


def chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------
# SKINPORT
# ---------------------------
def fetch_skinport_history(titles: List[str] | None = None, currency: str = "USD") -> Dict[str, dict]:
    params = {
        "app_id": APP_ID,
        "currency": currency,
    }
    if titles:
        params["market_hash_name"] = ",".join(titles)

    headers = {
        "Accept-Encoding": "br",
        "User-Agent": "cs2-skin-radar/3.0"
    }

    r = requests.get(SKINPORT_HISTORY_URL, params=params, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()

    out: Dict[str, dict] = {}
    for item in data:
        name = item.get("market_hash_name")
        if not name:
            continue

        last24 = item.get("last_24_hours") or {}
        last7 = item.get("last_7_days") or {}
        last30 = item.get("last_30_days") or {}

        current_price = last24.get("avg") or last7.get("avg") or 0
        avg30 = last30.get("avg") or last7.get("avg") or current_price or 0
        vol30 = last30.get("volume") or 0

        out[name] = {
            "source": "Skinport",
            "currency": currency,
            "current_price": float(current_price or 0),
            "avg30": float(avg30 or 0),
            "volume30": int(vol30 or 0),
            "item_page": item.get("item_page"),
            "market_page": item.get("market_page"),
        }

    return out


# ---------------------------
# CSFLOAT
# ---------------------------
def fetch_csfloat_lowest_for_titles(
    titles: List[str],
    pause_seconds: float = 0.12,
    only_buy_now: bool = True
) -> Dict[str, dict]:
    """
    Consulta o menor listing público por market_hash_name.
    A API é pública para GET listings; preço/filtros são documentados em cents.
    """
    session = requests.Session()
    out: Dict[str, dict] = {}

    for title in titles:
        params = {
            "market_hash_name": title,
            "limit": 1,
            "sort_by": "lowest_price",
        }
        if only_buy_now:
            params["type"] = "buy_now"

        try:
            r = session.get(CSFLOAT_LISTINGS_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            if isinstance(data, list) and data:
                listing = data[0]
                price_cents = listing.get("price")
                if price_cents is None:
                    continue

                out[title] = {
                    "source": "CSFloat",
                    "currency": "USD",
                    "lowest_listing": float(price_cents) / 100.0,
                    "listing_id": listing.get("id"),
                    "url": f"https://csfloat.com/search?market_hash_name={title}"
                }

        except Exception as e:
            print(f"CSFloat falhou em {title}: {e}")

        time.sleep(pause_seconds)

    return out


# ---------------------------
# DMARKET
# ---------------------------
def _dmarket_signed_headers(method: str, path: str, body: str = "") -> dict | None:
    api_key = os.getenv("DMARKET_API_KEY", "").strip()
    secret_key = os.getenv("DMARKET_API_SECRET", "").strip()

    if not api_key or not secret_key:
        return None

    nonce = str(int(time.time()))
    string_to_sign = method + path + body + nonce
    signature = crypto_sign(string_to_sign.encode("utf-8"), bytes.fromhex(secret_key))[:64].hex()

    return {
        "X-Api-Key": api_key,
        "X-Request-Sign": f"dmar ed25519 {signature}",
        "X-Sign-Date": nonce,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "cs2-skin-radar/3.0"
    }


def fetch_dmarket_aggregated_prices(titles: List[str]) -> Dict[str, dict]:
    """
    Usa POST /marketplace-api/v1/aggregated-prices.
    A docs usa 'filter.game' e mostra identificadores inconsistentes em seções diferentes,
    então tentamos 'csgo' e depois 'a8db'.
    """
    if not titles:
        return {}

    path = "/marketplace-api/v1/aggregated-prices"
    out: Dict[str, dict] = {}

    for batch in chunks(titles, 50):
        payload_base = {
            "limit": str(len(batch)),
            "filter": {
                "titles": batch
            }
        }

        response_json = None

        for game_id in ("csgo", "a8db"):
            payload = {
                **payload_base,
                "filter": {
                    "game": game_id,
                    "titles": batch
                }
            }
            body = json.dumps(payload, separators=(",", ":"))
            headers = _dmarket_signed_headers("POST", path, body)

            if headers is None:
                return {}

            r = requests.post(DMARKET_ROOT + path, data=body, headers=headers, timeout=40)

            if r.status_code == 200:
                response_json = r.json()
                break

            print(f"DMarket tentou game={game_id} e respondeu {r.status_code}: {r.text[:160]}")

        if not response_json:
            continue

        for item in response_json.get("aggregatedPrices", []):
            title = item.get("title")
            if not title:
                continue

            offer_best = item.get("offerBestPrice") or {}
            order_best = item.get("orderBestPrice") or {}

            out[title] = {
                "source": "DMarket",
                "currency": offer_best.get("Currency") or order_best.get("Currency") or "USD",
                "offer_best_price": float(offer_best.get("Amount") or 0),
                "offer_count": int(item.get("offerCount") or 0),
                "order_best_price": float(order_best.get("Amount") or 0),
                "order_count": int(item.get("orderCount") or 0),
                "url": f"https://dmarket.com/ingame-items/item-list/csgo-skins?title={title}"
            }

    return out


# ---------------------------
# MERGE
# ---------------------------
def merge_market_data(
    skinport_map: Dict[str, dict],
    csfloat_map: Dict[str, dict],
    dmarket_map: Dict[str, dict]
) -> List[dict]:
    merged = []

    all_titles = sorted(set(skinport_map) | set(csfloat_map) | set(dmarket_map))

    for title in all_titles:
        sp = skinport_map.get(title, {})
        cf = csfloat_map.get(title, {})
        dm = dmarket_map.get(title, {})

        buy_candidates = []
        sell_candidates = []

        if sp.get("current_price", 0) > 0:
            buy_candidates.append(("Skinport", sp["current_price"]))
            sell_candidates.append(("Skinport", sp["current_price"]))

        if cf.get("lowest_listing", 0) > 0:
            buy_candidates.append(("CSFloat", cf["lowest_listing"]))
            sell_candidates.append(("CSFloat", cf["lowest_listing"]))

        if dm.get("offer_best_price", 0) > 0:
            buy_candidates.append(("DMarket", dm["offer_best_price"]))

        if dm.get("order_best_price", 0) > 0:
            sell_candidates.append(("DMarket", dm["order_best_price"]))

        best_buy_site, best_buy_price = (None, 0.0)
        best_sell_site, best_sell_price = (None, 0.0)

        if buy_candidates:
            best_buy_site, best_buy_price = min(buy_candidates, key=lambda x: x[1])

        if sell_candidates:
            best_sell_site, best_sell_price = max(sell_candidates, key=lambda x: x[1])

        spread_pct = 0.0
        if best_buy_price > 0 and best_sell_price > 0:
            spread_pct = ((best_sell_price - best_buy_price) / best_buy_price) * 100.0

        merged.append({
            "skin": title,
            "skinport_price": sp.get("current_price", 0.0),
            "skinport_avg30": sp.get("avg30", 0.0),
            "skinport_vol30": sp.get("volume30", 0),
            "csfloat_price": cf.get("lowest_listing", 0.0),
            "dmarket_offer_best": dm.get("offer_best_price", 0.0),
            "dmarket_order_best": dm.get("order_best_price", 0.0),
            "best_buy_site": best_buy_site or "-",
            "best_buy_price": round(best_buy_price, 2) if best_buy_price else 0.0,
            "best_sell_site": best_sell_site or "-",
            "best_sell_price": round(best_sell_price, 2) if best_sell_price else 0.0,
            "spread_pct": round(spread_pct, 2),
        })

    merged.sort(key=lambda x: x["spread_pct"], reverse=True)
    return merged
