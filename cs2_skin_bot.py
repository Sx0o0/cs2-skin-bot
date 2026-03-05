import requests
import csv
import os
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, List, Tuple

# -----------------------------
# CONFIG
# -----------------------------
API_URL = "https://api.skinport.com/v1/prices"
HISTORY_FILE = "skin_history.csv"
OUTPUT_HTML = "index.html"

CURRENCY = "BRL"
APP_ID = 730

# Quantas skins mostrar no dashboard (top por volume)
MAX_ITEMS = 120

# Filtros para evitar lixo/itens sem liquidez
MIN_PRICE = 1.0
MAX_PRICE = 1000.0
MIN_VOLUME = 10  # aumenta se quiser só itens bem líquidos

# "Força" das previsões: lucro estimado (reversão à média) é limitado pra não exagerar
MAX_EXPECTED_PROFIT_PCT = 30.0

# Se você quiser sempre incluir algumas skins específicas (mesmo se não entrarem no top volume):
WHITELIST = {
    "AK-47 | Slate (Field-Tested)",
    "AK-47 | Ice Coaled (Field-Tested)",
    "USP-S | Cortex (Field-Tested)",
    "Glock-18 | Vogue (Field-Tested)",
    "M4A1-S | Night Terror (Field-Tested)",
}

# -----------------------------
# DATA FETCH
# -----------------------------
def fetch_market() -> List[dict]:
    params = {
        "app_id": APP_ID,
        "currency": CURRENCY,
        "tradable": 1
    }
    try:
        r = requests.get(API_URL, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print("Erro API:", e)
        return []


def pick_items(data: List[dict]) -> List[dict]:
    """
    Seleciona mais skins automaticamente:
    - filtra por preço e volume
    - pega TOP MAX_ITEMS por volume
    - adiciona whitelist (se existir no retorno)
    """
    cleaned = []
    by_name = {}

    for it in data:
        name = it.get("market_hash_name")
        if not name:
            continue

        # A Skinport geralmente fornece "price" e "volume"
        price = it.get("price")
        volume = it.get("volume", 0)

        # alguns retornam como string
        try:
            price_f = float(price) if price is not None else None
        except:
            price_f = None

        try:
            volume_i = int(volume) if volume is not None else 0
        except:
            volume_i = 0

        if price_f is None:
            continue

        if not (MIN_PRICE <= price_f <= MAX_PRICE):
            continue

        if volume_i < MIN_VOLUME and name not in WHITELIST:
            continue

        row = {
            "market_hash_name": name,
            "price": price_f,
            "volume": volume_i,
        }
        cleaned.append(row)
        by_name[name] = row

    # Top por volume
    top = sorted(cleaned, key=lambda x: x["volume"], reverse=True)[:MAX_ITEMS]

    # Garante whitelist
    for w in WHITELIST:
        if w in by_name and all(x["market_hash_name"] != w for x in top):
            top.append(by_name[w])

    # remove duplicados
    seen = set()
    uniq = []
    for x in top:
        n = x["market_hash_name"]
        if n in seen:
            continue
        seen.add(n)
        uniq.append(x)

    return uniq


# -----------------------------
# HISTORY
# -----------------------------
def update_history(date_str: str, skin: str, price: float) -> None:
    file_exists = os.path.isfile(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["date", "skin", "price"])
        w.writerow([date_str, skin, price])


def load_history() -> pd.DataFrame:
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame(columns=["date", "skin", "price"])

    df = pd.read_csv(HISTORY_FILE)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["date", "skin", "price"])
    df = df.sort_values(["skin", "date"])
    return df


# -----------------------------
# METRICS
# -----------------------------
def moving_avg(series: pd.Series, window: int) -> float:
    if len(series) < max(2, window):
        return float(series.mean()) if len(series) else float("nan")
    return float(series.tail(window).mean())


def avg_daily_change_pct(series: pd.Series, days: int = 14) -> float:
    """
    Média do |% change| dia-a-dia para estimar "velocidade" do preço.
    """
    if len(series) < 3:
        return float("nan")
    s = series.tail(days + 1)
    pct = s.pct_change().abs().dropna()
    if pct.empty:
        return float("nan")
    return float(pct.mean() * 100.0)


def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def expected_profit_pct(current: float, avg30: float) -> float:
    """
    Heurística: se preço atual está abaixo da média 30d,
    o lucro "estimado" é reverter até essa média.
    Se está acima, lucro esperado ~ 0 (ou negativo se quiser).
    """
    if current <= 0 or pd.isna(avg30) or avg30 <= 0:
        return 0.0

    raw = ((avg30 - current) / current) * 100.0
    # Só consideramos upside (se estiver barato vs média)
    raw = max(0.0, raw)
    return round(clamp(raw, 0.0, MAX_EXPECTED_PROFIT_PCT), 2)


def hold_days_estimate(current: float, avg30: float, speed_pct_per_day: float) -> int:
    """
    Estima quantos dias até "bater" a média 30d, usando velocidade média (%/dia).
    """
    if current <= 0 or pd.isna(avg30) or avg30 <= 0:
        return 14

    gap_pct = ((avg30 - current) / current) * 100.0
    gap_pct = max(0.0, gap_pct)

    if gap_pct < 0.5:
        return 3

    if pd.isna(speed_pct_per_day) or speed_pct_per_day <= 0.1:
        # se não tem histórico bom, chuta um meio termo
        return int(clamp(21, 3, 60))

    days = gap_pct / speed_pct_per_day
    return int(clamp(round(days), 3, 60))


def signal_from_profit(profit_pct: float) -> Tuple[str, str]:
    """
    Decide BUY/HOLD/SELL pela % lucro estimada.
    """
    if profit_pct >= 8:
        return ("BUY", "green")
    if profit_pct >= 3:
        return ("HOLD", "yellow")
    return ("PASS", "red")


# -----------------------------
# HTML
# -----------------------------
def generate_html(rows: List[dict]) -> None:
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    # monta rows HTML
    body_rows = []
    for r in rows:
        body_rows.append(f"""
<tr>
  <td>{r["skin"]}</td>
  <td>R$ {r["price"]:.2f}</td>
  <td>{r["volume"]}</td>
  <td>{r["avg7"]:.2f}</td>
  <td>{r["avg30"]:.2f}</td>
  <td>{r["speed"]:.2f}%/dia</td>
  <td><b>{r["profit"]:.2f}%</b></td>
  <td>{r["hold_days"]} dias</td>
  <td class="{r["color"]}">{r["signal"]}</td>
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
<p class="muted">Atualizado: {updated} • Baseado em média 7d/30d + volume + histórico local</p>

<div class="bar">
  <input id="q" placeholder="Buscar skin..." oninput="filterRows()"/>
  <span class="chip">Ordene clicando nos títulos</span>
  <span class="chip">Sinal = BUY/HOLD/PASS</span>
</div>

<table id="t">
  <thead>
    <tr>
      <th onclick="sortTable(0)">Skin</th>
      <th onclick="sortTable(1)">Preço</th>
      <th onclick="sortTable(2)">Volume</th>
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
  Nota: "Lucro est." é heurístico (reversão à média 30d) e NÃO garante retorno.
</p>

<script>
let sortDir = 1;
let lastCol = -1;

function parseCellText(td) {{
  const txt = td.innerText.trim();
  // tenta número com BRL / % / "dias"
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
    # Data de hoje (UTC simples) para gravar no CSV estável
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    market = fetch_market()
    if not market:
        print("API vazia")
        return

    selected = pick_items(market)
    if not selected:
        print("Nada selecionado após filtros")
        return

    hist_df = load_history()

    # Atualiza histórico
    for it in selected:
        update_history(today, it["market_hash_name"], it["price"])

    # Recarrega histórico com os novos pontos
    hist_df = load_history()

    out_rows = []
    for it in selected:
        skin = it["market_hash_name"]
        price = float(it["price"])
        volume = int(it.get("volume", 0))

        s = hist_df[hist_df["skin"] == skin]["price"]

        avg7 = moving_avg(s, 7)
        avg30 = moving_avg(s, 30)
        speed = avg_daily_change_pct(s, 14)

        profit = expected_profit_pct(price, avg30)
        hold_days = hold_days_estimate(price, avg30, speed)
        sig, color = signal_from_profit(profit)

        out_rows.append({
            "skin": skin,
            "price": price,
            "volume": volume,
            "avg7": 0.0 if pd.isna(avg7) else float(avg7),
            "avg30": 0.0 if pd.isna(avg30) else float(avg30),
            "speed": 0.0 if pd.isna(speed) else float(speed),
            "profit": profit,
            "hold_days": hold_days,
            "signal": sig,
            "color": color
        })

    # Ordena pelo maior lucro estimado e depois por volume
    out_rows.sort(key=lambda r: (r["profit"], r["volume"]), reverse=True)

    generate_html(out_rows)
    print("Ranking atualizado:", len(out_rows), "skins")


if __name__ == "__main__":
    main()
