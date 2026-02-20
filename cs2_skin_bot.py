import requests
import csv
import os
import pandas as pd
from datetime import datetime


SKINS = {
    "AK-47 | Slate (Field-Tested)",
    "AK-47 | Ice Coaled (Field-Tested)",
    "USP-S | Cortex (Field-Tested)",
    "Glock-18 | Vogue (Field-Tested)",
    "M4A1-S | Night Terror (Field-Tested)"
}

API_URL = "https://api.skinport.com/v1/prices"
HISTORY_FILE = "skin_history.csv"

MIN_PRICE = 1
MAX_PRICE = 1000


def fetch_prices():

    params = {
        "app_id": 730,
        "currency": "BRL"
    }

    try:
        response = requests.get(API_URL, params=params)
        data = response.json()

        if isinstance(data, list):
            return data

    except Exception as e:
        print("Erro API:", e)

    return []


def update_history(skin, price):

    file_exists = os.path.isfile(HISTORY_FILE)

    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        if not file_exists:
            writer.writerow(["date", "skin", "price"])

        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"),
            skin,
            price
        ])


def load_history():

    if not os.path.exists(HISTORY_FILE):
        return {}

    df = pd.read_csv(HISTORY_FILE)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.sort_values("date")

    history = {}

    for skin in df["skin"].unique():
        skin_data = df[df["skin"] == skin]
        history[skin] = skin_data["price"].tolist()

    return history


def calculate_score(skin, current_price, history):

    score = 0

    if skin in history and len(history[skin]) >= 7:
        last_7 = history[skin][-7:]
        moving_avg = sum(last_7) / len(last_7)

        trend = ((current_price - moving_avg) / moving_avg) * 100
        score += trend

    return round(score, 2)


def generate_html(results):

    html = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <title>CS2 Skin Ranking</title>
        <style>
            body {{ font-family: Arial; background: #111; color: white; padding: 40px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ padding: 10px; }}
            th {{ background: #222; }}
            tr:nth-child(even) {{ background: #1a1a1a; }}
        </style>
    </head>
    <body>
        <h1>🔥 CS2 Skin Ranking</h1>
        <p>Atualizado: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
        <table border="1">
            <tr>
                <th>Skin</th>
                <th>Preço</th>
                <th>Score</th>
            </tr>
    """

    for skin, price, score in results:
        html += f"""
        <tr>
            <td>{skin}</td>
            <td>R$ {price}</td>
            <td>{score}</td>
        </tr>
        """

    html += """
        </table>
    </body>
    </html>
    """

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)


def main():

    history = load_history()
    results = []

    data = fetch_prices()

    if not data:
        print("API vazia")
        return

    for item in data:

        name = item.get("market_hash_name")

        if name not in SKINS:
            continue

        price = item.get("avg_price")

        if not price:
            continue

        price = float(price)

        if not (MIN_PRICE <= price <= MAX_PRICE):
            continue

        update_history(name, price)

        score = calculate_score(name, price, history)

        results.append((name, price, score))

    results.sort(key=lambda x: x[2], reverse=True)

    print("Ranking atualizado")

    generate_html(results)


if __name__ == "__main__":
    main()
