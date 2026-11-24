import os
import json
import urllib.request
from urllib.parse import urlencode

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}/"

def request(method, data=None):
    body = urlencode(data or {}).encode()
    req = urllib.request.Request(API + method, data=body)
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.loads(response.read())

def send_message(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
    request("sendMessage", data)

keyboard = {
    "keyboard": [
        [{"text": "✨ Возможности"}, {"text": "✍️ Создать пост"}],
        [{"text": "💡 Идеи для контента"}, {"text": "👤 Мой профиль"}]
    ],
    "resize_keyboard": True
}

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text == "/start":
        send_message(
            chat_id,
            "⚡ VORTEX\n\nТвой цифровой помощник.\nВыбери нужный раздел:",
            keyboard
        )
    elif text == "✨ Возможности":
        send_message(chat_id, "✨ Возможности VORTEX:\n\n• Создание постов\n• Идеи для контента\n• Помощь с текстами")
    elif text == "✍️ Создать пост":
        send_message(chat_id, "✍️ Напиши тему поста, и я подготовлю основу!")
    elif text == "💡 Идеи для контента":
        send_message(chat_id, "💡 Напиши свою нишу, и мы подберём идеи для контента!")
    elif text == "👤 Мой профиль":
        send_message(chat_id, f"👤 Твой профиль\nID: {chat_id}")
    else:
        send_message(chat_id, "Я получил твоё сообщение! Выбери раздел в меню.")

offset = 0

while True:
    try:
        updates = request("getUpdates", {
            "offset": offset,
            "timeout": 30
        })

        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            if "message" in update:
                handle_message(update["message"])

    except Exception as error:
        print("Ошибка:", error)
