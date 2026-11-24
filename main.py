import os
import json
import urllib.request
from urllib.parse import urlencode

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}/"

def send_message(chat_id, text):
    data = urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(API + "sendMessage", data=data)
    urllib.request.urlopen(req)

offset = 0

while True:
    try:
        url = API + "getUpdates?" + urlencode({
            "offset": offset,
            "timeout": 30
        })
        with urllib.request.urlopen(url, timeout=40) as response:
            updates = json.loads(response.read())

        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message", {})
            chat = message.get("chat", {})
            text = message.get("text", "")

            if not chat or not text:
                continue

            if text == "/start":
                send_message(chat["id"], "Привет! Я VORTEX 🤖 Твой AI-помощник. Скоро здесь появятся мои функции!")
            else:
                send_message(chat["id"], "Я получил твоё сообщение: " + text)

    except Exception as error:
        print("Ошибка:", error)
