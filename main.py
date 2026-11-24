
import os
import json
import urllib.request
import urllib.error
from urllib.parse import urlencode

TOKEN = os.environ["BOT_TOKEN"]
GROQ_KEY = os.environ["GROQ_API_KEY"]

TG_API = f"https://api.telegram.org/bot{TOKEN}/"
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-20b"

user_states = {}
chat_histories = {}

keyboard = {
    "keyboard": [
        [{"text": "✨ Возможности"}, {"text": "✍️ Создать пост"}],
        [{"text": "💡 Идеи для контента"}, {"text": "👤 Мой профиль"}]
    ],
    "resize_keyboard": True
}

style_keyboard = {
    "keyboard": [
        [{"text": "💼 Деловой"}, {"text": "🔥 Продающий"}],
        [{"text": "🤝 Дружеский"}, {"text": "❌ Отмена"}]
    ],
    "resize_keyboard": True,
    "one_time_keyboard": True
}


def tg_request(method, data=None):
    body = urlencode(data or {}).encode()
    req = urllib.request.Request(TG_API + method, data=body)

    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read())


def send_message(chat_id, text, menu=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if menu:
        data["reply_markup"] = json.dumps(
            menu, ensure_ascii=False
        )

    tg_request("sendMessage", data)

def save_history():
    with open("history.json", "w", encoding="utf-8") as file:
        json.dump(chat_histories, file, ensure_ascii=False)


def load_history():
    global chat_histories
    try:
        with open("history.json", "r", encoding="utf-8") as file:
            chat_histories = {
                int(key): value
                for key, value in json.load(file).items()
            }
    except (FileNotFoundError, json.JSONDecodeError):
        chat_histories = {}
def ask_ai(chat_id, prompt):
    history = chat_histories.get(chat_id, [])

    messages = [
        {
            "role": "system",
            "content": (
                "Ты VORTEX, полезный ИИ-помощник. "
                "Отвечай на русском языке. "
                "Помогай создавать качественные посты, "
                "идеи для бизнеса и контента. "
                "Пиши понятно, без лишней воды. "
                "Не выдумывай факты."
            )
        }
    ]

    messages.extend(history[-8:])
    messages.append({
        "role": "user",
        "content": prompt
    })

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_completion_tokens": 1200
    }

    req = urllib.request.Request(
        GROQ_API,
        data=json.dumps(payload).encode(),
        headers={
    "Content-Type": "application/json",
    "Authorization": f"Bearer {GROQ_KEY}",
    "User-Agent": "VORTEX/1.0"
}
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read())

        answer = result["choices"][0]["message"]["content"]

        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": answer})
        chat_histories[chat_id] = history[-10:]
save_history()
        return answer.strip()

    except urllib.error.HTTPError as error:
        details = error.read().decode(errors="ignore")
        print("Groq HTTP error:", error.code, details)
        if error.code == 429:
            return "Сейчас превышен бесплатный лимит ИИ. Попробуй немного позже."
        return "Не получилось получить ответ от ИИ. Попробуй ещё раз."

    except Exception as error:
        print("Groq error:", error)
        return "Что-то пошло не так. Попробуй ещё раз."


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if not text:
        return

    if text == "/start":
        user_states.pop(chat_id, None)
        chat_histories.pop(chat_id, None)
        send_message(
            chat_id,
            "⚡ VORTEX\n\nТвой ИИ-помощник.\n\n"
            "Создавай посты, придумывай идеи "
            "и задавай любые вопросы.",
            keyboard
        )
        return

    if text in ["/cancel", "❌ Отмена"]:
        user_states.pop(chat_id, None)
        send_message(chat_id, "Действие отменено.", keyboard)
        return

    if text == "✨ Возможности":
        send_message(
            chat_id,
            "✨ Возможности VORTEX:\n\n"
            "• Создание постов с помощью ИИ\n"
            "• Идеи для контента\n"
            "• Помощь с текстами\n"
            "• Ответы на вопросы\n"
            "• Разные стили написания",
            keyboard
        )
        return

    if text == "✍️ Создать пост":
        user_states[chat_id] = {"step": "topic"}
        send_message(
            chat_id,
            "✍️ На какую тему создаём пост?\n\n"
            "Напиши тему одним сообщением.",
            {"keyboard": [[{"text": "❌ Отмена"}]],
             "resize_keyboard": True}
        )
        return

    if text == "💡 Идеи для контента":
        user_states[chat_id] = {"step": "ideas"}
        send_message(
            chat_id,
            "💡 Для какой ниши нужны идеи?\n\n"
            "Например: магазин одежды, техника, "
            "спорт, личный блог.",
            {"keyboard": [[{"text": "❌ Отмена"}]],
             "resize_keyboard": True}
        )
        return

    if text == "👤 Мой профиль":
        send_message(
            chat_id,
            f"👤 Твой профиль\nID: {chat_id}\n\n"
            "Ты пользуешься VORTEX.",
            keyboard
        )
        return

    state = user_states.get(chat_id, {})

    if state.get("step") == "topic":
        user_states[chat_id] = {
            "step": "style",
            "topic": text
        }
        send_message(
            chat_id,
            "Выбери стиль будущего поста:",
            style_keyboard
        )
        return

    if state.get("step") == "style":
        styles = {
            "💼 Деловой": "деловой и экспертный",
            "🔥 Продающий": "продающий, убедительный",
            "🤝 Дружеский": "дружеский, простой и живой"
        }

        if text not in styles:
            send_message(
                chat_id,
                "Выбери один из предложенных стилей.",
                style_keyboard
            )
            return

        topic = state["topic"]
        style = styles[text]
        user_states.pop(chat_id, None)

        send_message(chat_id, "⚡ VORTEX создаёт твой пост...")

        answer = ask_ai(
            chat_id,
            f"Создай готовый пост на тему: {topic}. "
            f"Стиль: {style}. Добавь цепляющий заголовок, "
            "основной текст и призыв к действию. "
            "Не добавляй выдуманные факты."
        )

        send_message(chat_id, answer, keyboard)
        return

    if state.get("step") == "ideas":
        user_states.pop(chat_id, None)
        send_message(chat_id, "💡 Подбираю идеи...")
        answer = ask_ai(
            chat_id,
            f"Придумай 10 интересных идей для контента "
            f"в нише: {text}. К каждой добавь короткое описание."
        )
        send_message(chat_id, answer, keyboard)
        return

    send_message(chat_id, "⚡ Думаю над ответом...")

    answer = ask_ai(chat_id, text)
    send_message(chat_id, answer, keyboard)


offset = 0
load_history()
while True:
    try:
        updates = tg_request("getUpdates", {
            "offset": offset,
            "timeout": 30
        })

        for update in updates.get("result", []):
            offset = update["update_id"] + 1

            if "message" in update:
                handle_message(update["message"])

    except Exception as error:
        print("Ошибка:", error)
