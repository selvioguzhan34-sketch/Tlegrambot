import os
import time
import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

API = f"https://api.telegram.org/bot{TOKEN}"

offset = 0

print("Crypto Jet Telegram botu başladı!")

while True:
    try:
        response = requests.get(
            f"{API}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )

        data = response.json()

        for update in data.get("result", []):
            offset = update["update_id"] + 1

            message = update.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            text = message.get("text", "")

            if text == "/start":
                reply = "🚀 Crypto Jet çalışıyor!"

            elif text == "/btc":
                btc = requests.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "bitcoin", "vs_currencies": "usd"},
                    timeout=10
                ).json()

                price = btc["bitcoin"]["usd"]
                reply = f"₿ Bitcoin fiyatı: ${price:,.2f}"

            else:
                reply = f"Mesajını aldım: {text}"

            requests.post(
                f"{API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": reply
                },
                timeout=10
            )

    except Exception as e:
        print("Hata:", e)
        time.sleep(5)
