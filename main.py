
import os
import json
import time
import uuid
import mimetypes
import urllib.request
import urllib.error
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["BOT_TOKEN"]
GROQ_KEY = os.environ["GROQ_API_KEY"]

TG_API = f"https://api.telegram.org/bot{TOKEN}/"
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
GROQ_AUDIO_API = "https://api.groq.com/openai/v1/audio/transcriptions"

MODEL = "openai/gpt-oss-20b"
WHISPER_MODEL = "whisper-large-v3-turbo"

CHANNEL = "@VORTEX_RELAX"
FREE_REQUESTS = 5
TRIAL_DAYS = 7
PRICE = 99

user_states = {}
chat_histories = {}
users = {}

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
    data = {"chat_id": chat_id, "text": text}

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


def save_users():
    with open("users.json", "w", encoding="utf-8") as file:
        json.dump(users, file, ensure_ascii=False)


def load_users():
    global users

    try:
        with open("users.json", "r", encoding="utf-8") as file:
            users = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        users = {}


def get_user(chat_id):
    key = str(chat_id)

    if key not in users:
        users[key] = {
            "requests": 0,
            "trial_until": None,
            "paid_until": None
        }
        save_users()

    return users[key]


def is_subscribed(chat_id):
    try:
        result = tg_request("getChatMember", {
            "chat_id": CHANNEL,
            "user_id": chat_id
        })

        member = result.get("result", {})
        status = member.get("status")

        return (
            status in ["creator", "administrator", "member"]
            or (
                status == "restricted"
                and member.get("is_member", False)
            )
        )

    except Exception as error:
        print("Subscription check error:", error)
        return False


def subscription_message(chat_id):
    menu = {
        "inline_keyboard": [
            [{
                "text": "📢 Подписаться на канал",
                "url": "https://t.me/VORTEX_RELAX"
            }],
            [{
                "text": "✅ Проверить подписку",
                "callback_data": "check_subscription"
            }]
        ]
    }

    send_message(
        chat_id,
        "⚡ VORTEX\n\n"
        "Твои 5 бесплатных запросов закончились!\n\n"
        "Подпишись на наш канал, чтобы получить "
        "7 дней бесплатного доступа.",
        menu
    )


def payment_message(chat_id):
    send_message(
        chat_id,
        "⚡ VORTEX PREMIUM\n\n"
        "Твой бесплатный период закончился.\n\n"
        "Стоимость доступа: 99 ₽ / 30 дней.\n\n"
        "Скоро здесь появится возможность оплаты.\n"
        "Пока платная подписка ещё не подключена."
    )


def check_access(chat_id):
    user = get_user(chat_id)

    paid_until = user.get("paid_until")
    trial_until = user.get("trial_until")

    now = datetime.now(timezone.utc)

    if paid_until:
        if now < datetime.fromisoformat(paid_until):
            return "paid"

    if trial_until:
        if now < datetime.fromisoformat(trial_until):
            if is_subscribed(chat_id):
                return "trial"

            return "channel"

        return "expired"

    if user["requests"] < FREE_REQUESTS:
        return "free"

    return "channel"


def can_use_ai(chat_id):
    access = check_access(chat_id)

    if access in ["free", "trial", "paid"]:
        return True

    if access == "channel":
        subscription_message(chat_id)
        return False

    if access == "expired":
        payment_message(chat_id)
        return False

    return False


def count_request(chat_id):
    user = get_user(chat_id)

    if not user.get("trial_until") and not user.get("paid_until"):
        user["requests"] += 1
        save_users()


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
    messages.append({"role": "user", "content": prompt})

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
            return "Превышен лимит ИИ. Попробуй позже."

        return "Не получилось получить ответ от ИИ."

    except Exception as error:
        print("Groq error:", error)
        return "Что-то пошло не так. Попробуй ещё раз."


# 🎙️ РАСПОЗНАВАНИЕ ГОЛОСОВЫХ СООБЩЕНИЙ

def transcribe_voice(file_id):
    try:
        file_info = tg_request("getFile", {
            "file_id": file_id
        })

        if not file_info.get("ok"):
            return None

        file_path = file_info["result"]["file_path"]

        download_url = (
            f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        )

        with urllib.request.urlopen(download_url, timeout=60) as response:
            audio_data = response.read()

        if not audio_data or len(audio_data) > 20 * 1024 * 1024:
            return None

        boundary = "----VORTEX" + uuid.uuid4().hex

        filename = os.path.basename(file_path) or "voice.ogg"
        content_type = mimetypes.guess_type(filename)[0] or "audio/ogg"

        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="model"\r\n\r\n'
            f"{WHISPER_MODEL}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + audio_data + (
            f"\r\n--{boundary}--\r\n"
        ).encode()

        req = urllib.request.Request(
            GROQ_AUDIO_API,
            data=body,
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": f"multipart/form-data; boundary={boundary}"
            }
        )

        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read())

        recognized = result.get("text", "").strip()

        return recognized if recognized else None

    except urllib.error.HTTPError as error:
        details = error.read().decode(errors="ignore")
        print("Whisper HTTP error:", error.code, details)
        return None

    except Exception as error:
        print("Voice recognition error:", error)
        return None


def handle_callback(callback):
    chat_id = callback["message"]["chat"]["id"]
    callback_id = callback["id"]

    tg_request("answerCallbackQuery", {
        "callback_query_id": callback_id
    })

    if callback.get("data") != "check_subscription":
        return

    if not is_subscribed(chat_id):
        send_message(
            chat_id,
            "❌ Подписка пока не найдена.\n"
            "Подпишись на канал и нажми проверку ещё раз."
        )
        return

    user = get_user(chat_id)

    if not user.get("trial_until"):
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=TRIAL_DAYS)

        user["trial_until"] = end.isoformat()
        save_users()

        send_message(
            chat_id,
            "🎉 Подписка подтверждена!\n\n"
            "Тебе начислено 7 дней бесплатного доступа.\n"
            "Приятного пользования VORTEX!",
            keyboard
        )
    else:
        send_message(
            chat_id,
            "✅ Подписка подтверждена!\n"
            "Можешь продолжать пользоваться VORTEX.",
            keyboard
        )


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    # 🎙️ Обработка голосовых сообщений
    if message.get("voice"):
        if not can_use_ai(chat_id):
            return

        send_message(chat_id, "🎙️ VORTEX слушает тебя...")

        file_id = message["voice"]["file_id"]
        recognized_text = transcribe_voice(file_id)

        if not recognized_text:
            send_message(
                chat_id,
                "❌ Не удалось распознать голосовое.\n"
                "Попробуй отправить ещё раз.",
                keyboard
            )
            return

        text = recognized_text

        send_message(
            chat_id,
            f"📝 Я услышал:\n\n{text}\n\n⚡ Обрабатываю..."
        )

        # После распознавания голосовое обрабатывается
        # как обычное сообщение ниже.

    if not text:
        return

    if text == "/start":
        user_states.pop(chat_id, None)

        get_user(chat_id)

        send_message(
            chat_id,
            "⚡ VORTEX\n\n"
            "Твой ИИ-помощник.\n\n"
            "Создавай посты, придумывай идеи, "
            "задавай вопросы или просто отправляй голосовые сообщения 🎙️",
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
            "• Разные стили написания\n"
            "• 🎙️ Распознавание голосовых сообщений",
            keyboard
        )
        return

    if text == "✍️ Создать пост":
        user_states[chat_id] = {"step": "topic"}

        send_message(
            chat_id,
            "✍️ На какую тему создаём пост?\n\n"
            "Напиши тему одним сообщением.",
            {
                "keyboard": [[{"text": "❌ Отмена"}]],
                "resize_keyboard": True
            }
        )
        return

    if text == "💡 Идеи для контента":
        user_states[chat_id] = {"step": "ideas"}

        send_message(
            chat_id,
            "💡 Для какой ниши нужны идеи?\n\n"
            "Например: магазин одежды, техника, спорт.",
            {
                "keyboard": [[{"text": "❌ Отмена"}]],
                "resize_keyboard": True
            }
        )
        return

    if text == "👤 Мой профиль":
        user = get_user(chat_id)
        access = check_access(chat_id)

        if access == "free":
            status = f"Бесплатные запросы: {user['requests']}/{FREE_REQUESTS}"
        elif access == "trial":
            status = "Статус: 7 дней бесплатного доступа"
        elif access == "paid":
            status = "Статус: PREMIUM"
        elif access == "expired":
            status = "Статус: доступ закончился"
        else:
            status = "Статус: требуется подписка на канал"

        send_message(
            chat_id,
            f"👤 Твой профиль\nID: {chat_id}\n\n{status}",
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

        if not can_use_ai(chat_id):
            return

        topic = state["topic"]
        style = styles[text]
        user_states.pop(chat_id, None)

        send_message(chat_id, "⚡ VORTEX создаёт твой пост...")

        answer = ask_ai(
            chat_id,
            f"Создай готовый пост на тему: {topic}. "
            f"Стиль: {style}. Добавь заголовок, "
            "основной текст и призыв к действию."
        )

        count_request(chat_id)
        send_message(chat_id, answer, keyboard)
        return

    if state.get("step") == "ideas":
        if not can_use_ai(chat_id):
            return

        user_states.pop(chat_id, None)

        send_message(chat_id, "💡 Подбираю идеи...")

        answer = ask_ai(
            chat_id,
            f"Придумай 10 интересных идей для контента "
            f"в нише: {text}. К каждой добавь описание."
        )

        count_request(chat_id)
        send_message(chat_id, answer, keyboard)
        return

    if not can_use_ai(chat_id):
        return

    send_message(chat_id, "⚡ Думаю над ответом...")

    answer = ask_ai(chat_id, text)

    count_request(chat_id)
    send_message(chat_id, answer, keyboard)


offset = 0

load_history()
load_users()

while True:
    try:
        updates = tg_request("getUpdates", {
            "offset": offset,
            "timeout": 30
        })

        for update in updates.get("result", []):
            offset = update["update_id"] + 1

            if "callback_query" in update:
                handle_callback(update["callback_query"])

            elif "message" in update:
                handle_message(update["message"])

    except Exception as error:
        print("Ошибка:", error)
        time.sleep(3)
