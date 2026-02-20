# ============================================================
# 🔥 BOT DE ANÁLISE CS2 - VERSÃO EVOLUÍDA
# ============================================================

# =========================
# 📦 IMPORTS
# =========================
import requests
import csv
import os
from datetime import datetime


# =========================
# ⚙ CONFIGURAÇÕES
# =========================
APPID = 730
MIN_PRICE = 5
MAX_PRICE = 40
HISTORY_FILE = "skin_history.csv"

CRASH_ALERT_PERCENT = -8  # queda maior que -8% ativa alerta forte

skins = [
    "AK-47 | Slate (Field-Tested)",
    "AK-47 | Ice Coaled (Field-Tested)",
    "M4A1-S | Night Terror (Field-Tested)",
    "M4A4 | Magnesium (Field-Tested)",
    "USP-S | Cortex (Field-Tested)",
    "Glock-18 | Vogue (Field-Tested)"
]


# ============================================================
# 🌐 BUSCAR DADOS DA STEAM
# ============================================================
def get_market_data(skin_name):
    url = "https://steamcommunity.com/market/priceoverview/"
    params = {
        "appid": APPID,
        "currency": 7,
        "market_hash_name": skin_name
    }

    response = requests.get(url, params=params)

    if response.status_code == 200:
        data = response.json()

        if data.get("success"):
            price = None
            volume = 0

            if "lowest_price" in data:
                price_str = data["lowest_price"]
                price = float(
                    price_str.replace("R$", "")
                    .replace(" ", "")
                    .replace(",", ".")
                )

            if "volume" in data:
                volume = int(
                    data["volume"]
                    .replace(",", "")
                    .replace(".", "")
                )

            return price, volume

    return None, 0


# ============================================================
# 📊 CARREGAR HISTÓRICO
# ============================================================
def load_history():
    history = {}
    existing_rows = []

    if not os.path.isfile(HISTORY_FILE):
        return history, existing_rows

    with open(HISTORY_FILE, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            skin = row["skin"]
            price = float(row["price"])
            date = row["date"]

            existing_rows.append((date, skin, price))

            if skin not in history:
                history[skin] = []

            history[skin].append(price)

    return history, existing_rows


# ============================================================
# 💾 SALVAR PREÇO (ANTI-DUPLICAÇÃO)
# ============================================================
def save_price(today, skin, price, existing_rows):
    # Verifica se já existe registro da mesma skin no mesmo dia
    for row_date, row_skin, _ in existing_rows:
        if row_date == today and row_skin == skin:
            return False  # Já registrado hoje

    file_exists = os.path.isfile(HISTORY_FILE)

    with open(HISTORY_FILE, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        if not file_exists:
            writer.writerow(["date", "skin", "price"])

        writer.writerow([today, skin, price])

    return True


# ============================================================
# 🧠 CÁLCULO DE SCORE AVANÇADO
# ============================================================
def calculate_score(skin, current_price, volume, history):
    score = 0

    if skin in history and len(history[skin]) >= 2:
        prices = history[skin]

        # --------- MÉDIA MÓVEL 7 DIAS ---------
        if len(prices) >= 7:
            last_7 = prices[-7:]
            moving_avg_7 = sum(last_7) / len(last_7)

            if moving_avg_7 > 0:
                trend_percent = ((current_price - moving_avg_7) / moving_avg_7) * 100
                score += trend_percent * 0.6

        # --------- VARIAÇÃO DIÁRIA ---------
        previous_price = prices[-1]

        if previous_price > 0:
            variation = ((current_price - previous_price) / previous_price) * 100
            score += variation * 1.2

            # --------- ALERTA DE QUEDA BRUSCA ---------
            if variation <= CRASH_ALERT_PERCENT:
                score += 5  # boost forte
                print(f"⚠️ ALERTA DE QUEDA: {skin} caiu {round(variation,2)}%")

    # --------- LIQUIDEZ ---------
    if volume > 1000:
        score += 3
    elif volume > 300:
        score += 2
    elif volume > 100:
        score += 1
    else:
        score -= 2

    # --------- PREÇO ESTRATÉGICO ---------
    if current_price < 15:
        score += 2

    return round(score, 2)


# ============================================================
# 🚦 GERAR SINAL
# ============================================================
def generate_signal(score):
    if score >= 8:
        return "🚀 OPORTUNIDADE AGRESSIVA"
    elif score >= 5:
        return "🟢 COMPRA FORTE"
    elif score >= 2:
        return "🟡 OBSERVAR"
    else:
        return "🔴 EVITAR"


# ============================================================
# 🚀 MAIN
# ============================================================
def main():
    print("Buscando preços...\n")

    history, existing_rows = load_history()
    today = datetime.now().strftime("%Y-%m-%d")

    results = []

    for skin in skins:
        price, volume = get_market_data(skin)

        if price and MIN_PRICE <= price <= MAX_PRICE:

            saved = save_price(today, skin, price, existing_rows)

            # Atualiza histórico apenas se salvou
            if saved:
                if skin not in history:
                    history[skin] = []
                history[skin].append(price)

            score = calculate_score(skin, price, volume, history)
            signal = generate_signal(score)

            results.append((skin, price, volume, score, signal))

    if not results:
        print("Nenhuma skin encontrada na faixa R$5–40.")
        return

    results.sort(key=lambda x: x[3], reverse=True)

    print("RANKING INTELIGENTE:\n")

    for skin, price, volume, score, signal in results:
        print(f"{skin}")
        print(f"Preço: R$ {price}")
        print(f"Volume: {volume}")
        print(f"Score: {score}")
        print(f"Sinal: {signal}")
        print("-" * 40)


# ============================================================
# ▶ EXECUÇÃO
# ============================================================
if __name__ == "__main__":
    print("BOT DE ANÁLISE CS2 AVANÇADO ATIVO\n")
    main()
