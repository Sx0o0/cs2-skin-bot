# ============================================================
# 📦 IMPORTS
# ============================================================

import requests
import csv
import os
import pandas as pd
from datetime import datetime


# ============================================================
# 🎯 CONFIGURAÇÃO DAS SKINS
# ============================================================

SKINS = {
    "AK-47 | Slate (Field-Tested)": "730/2/1031",
    "AK-47 | Ice Coaled (Field-Tested)": "730/2/1143",
    "USP-S | Cortex (Field-Tested)": "730/2/846",
    "Glock-18 | Vogue (Field-Tested)": "730/2/930",
    "M4A1-S | Night Terror (Field-Tested)": "730/2/1144"
}

API_URL = "https://api.skinport.com/v1/items"
HISTORY_FILE = "skin_history.csv"


# ============================================================
# 📡 BUSCAR DADOS DA API
# ============================================================

def fetch_data(market_hash_name):
    params = {
        "market_hash_name": market_hash_name,
        "app_id": 730,
        "currency": "BRL"
    }

    try:
        response = requests.get(API_URL, params=params)
        data = response.json()

        if data and isinstance(data, list):
            item = data[0]
            return item["min_price"] / 100, item["volume"]

    except Exception as e:
        print(f"Erro ao buscar dados: {e}")

    return None, None


# ============================================================
# 💾 SALVAR HISTÓRICO
# ============================================================

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


# ============================================================
# 📊 CARREGAR HISTÓRICO E ORGANIZAR
# ============================================================

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


# ============================================================
# 🧠 CALCULAR SCORE
# ============================================================

def calculate_score(skin, current_price, volume, history):

    score = 0

    if skin in history and len(history[skin]) >= 7:
        last_7 = history[skin][-7:]
        moving_avg_7 = sum(last_7) / len(last_7)

        trend_percent = ((current_price - moving_avg_7) / moving_avg_7) * 100
        score += trend_percent * 0.6

    # Volume influencia
    score += (volume / 100) * 0.4

    return round(score, 2)


# ============================================================
# 📈 GERAR SINAL
# ============================================================

def generate_signal(score):
    if score >= 15:
        return "COMPRA AGRESSIVA"
    elif score >= 5:
        return "OBSERVAR"
    else:
        return "EVITAR"


# ============================================================
# 🌐 GERAR SITE HTML
# ============================================================

def generate_html(results):

    html_content = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <title>CS2 Skin Ranking</title>
        <style>
            body {{ font-family: Arial; background-color: #111; color: white; padding: 40px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ padding: 12px; text-align: left; }}
            th {{ background-color: #222; }}
            tr:nth-child(even) {{ background-color: #1a1a1a; }}
            .green {{ color: #00ff88; }}
            .yellow {{ color: #ffcc00; }}
            .red {{ color: #ff4444; }}
        </style>
    </head>
    <body>
        <h1>🔥 CS2 Skin Ranking</h1>
        <p>Atualizado em: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
        <table border="1">
            <tr>
                <th>Skin</th>
                <th>Preço</th>
                <th>Volume</th>
                <th>Score</th>
                <th>Sinal</th>
            </tr>
    """

    for skin, price, volume, score, signal in results:

        if "AGRESSIVA" in signal:
            color = "green"
        elif "OBSERVAR" in signal:
            color = "yellow"
        else:
            color = "red"

        html_content += f"""
            <tr>
                <td>{skin}</td>
                <td>R$ {price}</td>
                <td>{volume}</td>
                <td>{score}</td>
                <td class="{color}">{signal}</td>
            </tr>
        """

    html_content += """
        </table>
    </body>
    </html>
    """

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)


# ============================================================
# 🚀 FUNÇÃO PRINCIPAL
# ============================================================

def main():

    history = load_history()
    results = []

    for skin in SKINS.keys():

        price, volume = fetch_data(skin)

        if price is None:
            continue

        update_history(skin, price)

        score = calculate_score(skin, price, volume, history)
        signal = generate_signal(score)

        results.append((skin, price, volume, score, signal))

    results.sort(key=lambda x: x[3], reverse=True)

    print("\n🔥 RANKING ATUAL:\n")

    for skin, price, volume, score, signal in results:
        print(f"{skin}")
        print(f"Preço: R$ {price}")
        print(f"Volume: {volume}")
        print(f"Score: {score}")
        print(f"Sinal: {signal}")
        print("-" * 40)

    # 🌐 Gera o site
    generate_html(results)


# ============================================================
# ▶️ EXECUÇÃO
# ============================================================

if __name__ == "__main__":
    main()
