import requests
import csv
import os
from datetime import datetime

APPID = 730
MIN_PRICE = 5
MAX_PRICE = 40
HISTORY_FILE = "skin_history.csv"

skins = [
    "AK-47 | Slate (Field-Tested)",
    "AK-47 | Ice Coaled (Field-Tested)",
    "M4A1-S | Night Terror (Field-Tested)",
    "M4A4 | Magnesium (Field-Tested)",
    "USP-S | Cortex (Field-Tested)",
    "Glock-18 | Vogue (Field-Tested)"
]


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
                volume = int(data["volume"].replace(",", "").replace(".", ""))

            return price, volume

    return None, 0


def save_price(date, skin, price):
    file_exists = os.path.isfile(HISTORY_FILE)

    with open(HISTORY_FILE, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["date", "skin", "price"])
        writer.writerow([date, skin, price])


def load_history():
    history = {}

    if not os.path.isfile(HISTORY_FILE):
        return history

    with open(HISTORY_FILE, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            skin = row["skin"]
            price = float(row["price"])

            if skin not in history:
                history[skin] = []

            history[skin].append(price)

    return history
    
def calculate_score(skin, current_price, volume, history):
    score = 0

    if skin in history and len(history[skin]) >= 7:
        last_7 = history[skin][-7:]
        moving_avg_7 = sum(last_7) / len(last_7)

        if moving_avg_7 > 0:
            trend_percent = ((current_price - moving_avg_7) / moving_avg_7) * 100
            score += trend_percent * 0.6

    return score
    
    if skin in history and len(history[skin]) >= 2:
        prices = history[skin]
        previous_price = prices[-1]

        variation = ((current_price - previous_price) / previous_price) * 100
        score += variation * 1.2

        if len(prices) >= 7:
            last_week = prices[-7:]
            weekly_trend = ((last_week[-1] - last_week[0]) / last_week[0]) * 100
            score += weekly_trend * 0.5

        if len(prices) >= 3:
            last_three = prices[-3:]
            short_trend = ((last_three[-1] - last_three[0]) / last_three[0]) * 100
            score += short_trend * 0.8

    # Liquidez
    if volume > 500:
        score += 3
    elif volume > 200:
        score += 2
    elif volume > 50:
        score += 1
    else:
        score -= 2

    # Preço baixo estratégico
    if current_price < 15:
        score += 2

    return round(score, 2)


def generate_signal(score):
    if score >= 6:
        return "🟢 COMPRA FORTE"
    elif score >= 2:
        return "🟡 OBSERVAR"
    else:
        return "🔴 EVITAR"


def main():
    print("Buscando preços...\n")

    history = load_history()
    today = datetime.now().strftime("%Y-%m-%d")
    results = []

    for skin in skins:
        price, volume = get_market_data(skin)

        if price and MIN_PRICE <= price <= MAX_PRICE:
            save_price(today, skin, price)
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


if __name__ == "__main__":
    print("BOT DE ANÁLISE CS2 ATIVO\n")
    main()
