import os
import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

url = f"https://api.telegram.org/bot{TOKEN}/getMe"
response = requests.get(url, timeout=10)

print(response.json())
