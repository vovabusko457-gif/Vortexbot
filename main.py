
import os
import json
import time
import uuid
import base64
import urllib.request
import urllib.error
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["BOT_TOKEN"]
GROQ_KEY = os.environ["GROQ_API_KEY"]

CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")

TG_API = f"https://api.telegram.org/bot{TOKEN}/"
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
GROQ_AUDIO_API = "https://api.groq.com/openai/v1/audio/transcriptions"

CF_IMAGE_API = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CF_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
)

MODEL = "openai/gpt-oss-20b"
WHISPER_MODEL = "whisper-large-v3-turbo"

CHANNEL = "@VORTEX_RELAX"

FREE_REQUESTS = 5
TRIAL_DAYS = 7

STARS_PRICE = 65
PREMIUM_DAYS = 30

user_states = {}
chat_histories = {}
users = {}

keyboard = {
    "keyboard": [
        [{"text": "✨ Возможности"}, {"text": "✍️ Создать пост"}],
        [{"text": "💡 Идеи для контента"}, {"text": "🎨 Создать картинку"}],
        [{"text": "👤 Мой профиль"}, {"text": "💎 Купить Premium"}]
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


# =========================
# TELEGRAM
# =========================

def tg_request(method, data=None):
    body = urlencode(data or {}).encode()

    req = urllib.request.Request(
        TG_API + method,
        data=body,
        headers={"User-Agent": "VORTEX-Bot/1.0"}
    )

    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read())


def send_message(chat_id, text, menu=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if menu:
        data["reply_markup"] = json.dumps(
            menu,
            ensure_ascii=False
        )

    tg_request("sendMessage", data)


def send_photo(chat_id, image_data, caption=""):
    boundary = "----VORTEX" + uuid.uuid4().hex

    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="photo"; filename="vortex.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + image_data + (
        f"\r\n--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        TG_API + "sendPhoto",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "VORTEX-Bot/1.0"
        }
    )

    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.loads(response.read())

    if not result.get("ok"):
        raise Exception(str(result))

    return result


# =========================
# СОХРАНЕНИЕ ДАННЫХ
# =========================

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
            "paid_until": None,
            "payment_charges": []
        }

    user = users[key]

    user.setdefault("requests", 0)
    user.setdefault("trial_until", None)
    user.setdefault("paid_until", None)
    user.setdefault("payment_charges", [])

    return user


def parse_date(value):
    if not value:
        return None

    try:
        result = datetime.fromisoformat(value)

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result

    except (ValueError, TypeError):
        return None


# =========================
# ПОДПИСКА И ДОСТУП
# =========================

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


# =========================
# TELEGRAM STARS
# =========================

def payment_message(chat_id):
    payload = f"vortex_premium_30:{chat_id}:{uuid.uuid4().hex}"

    prices = [{
        "label": "VORTEX PREMIUM на 30 дней",
        "amount": STARS_PRICE
    }]

    data = {
        "chat_id": chat_id,
        "title": "VORTEX PREMIUM",
        "description": (
            "30 дней полного доступа к VORTEX: "
            "ИИ, создание постов, идеи, голосовые "
            "и генерация изображений."
        ),
        "payload": payload,
        "currency": "XTR",
        "prices": json.dumps(prices, ensure_ascii=False)
    }

    try:
        result = tg_request("sendInvoice", data)

        if not result.get("ok"):
            print("Invoice error:", result)
            send_message(
                chat_id,
                "❌ Не удалось выставить счёт.\n"
                "Попробуй немного позже.",
                keyboard
            )
            return

        print("Invoice sent:", chat_id, payload)

    except Exception as error:
        print("Invoice sending error:", error)
        send_message(
            chat_id,
            "❌ Ошибка при создании счёта. Попробуй позже.",
            keyboard
        )


def handle_pre_checkout(query):
    query_id = query["id"]
    user_id = query["from"]["id"]
    payload = query.get("invoice_payload", "")
    currency = query.get("currency")
    amount = query.get("total_amount")

    parts = payload.split(":")

    valid_payload = (
        len(parts) == 3
        and parts[0] == "vortex_premium_30"
        and parts[1] == str(user_id)
    )

    valid_payment = (
        valid_payload
        and currency == "XTR"
        and amount == STARS_PRICE
    )

    if not valid_payment:
        tg_request("answerPreCheckoutQuery", {
            "pre_checkout_query_id": query_id,
            "ok": "false",
            "error_message": (
                "Счёт недействителен. "
                "Открой VORTEX и создай новый."
            )
        })
        print("Rejected pre-checkout:", user_id, payload)
        return

    tg_request("answerPreCheckoutQuery", {
        "pre_checkout_query_id": query_id,
        "ok": "true"
    })

    print("Pre-checkout approved:", user_id)


def activate_premium(message):
    chat_id = message["chat"]["id"]
    payment = message.get("successful_payment", {})

    currency = payment.get("currency")
    amount = payment.get("total_amount")
    payload = payment.get("invoice_payload", "")
    charge_id = payment.get("telegram_payment_charge_id")

    parts = payload.split(":")

    valid_payload = (
        len(parts) == 3
        and parts[0] == "vortex_premium_30"
        and parts[1] == str(chat_id)
    )

    if (
        not valid_payload
        or currency != "XTR"
        or amount != STARS_PRICE
        or not charge_id
    ):
        print("Invalid successful payment:", payment)
        send_message(
            chat_id,
            "❌ Не удалось подтвердить платёж автоматически. "
            "Обратись в поддержку VORTEX."
        )
        return

    user = get_user(chat_id)
    charges = user.get("payment_charges", [])

    if charge_id in charges:
        print("Duplicate payment ignored:", charge_id)
        return

    now = datetime.now(timezone.utc)
    old_paid_until = parse_date(user.get("paid_until"))

    start = now

    if old_paid_until and old_paid_until > now:
        start = old_paid_until

    end = start + timedelta(days=PREMIUM_DAYS)

    user["paid_until"] = end.isoformat()

    charges.append(charge_id)
    user["payment_charges"] = charges[-100:]

    save_users()

    print(
        "Premium activated:",
        chat_id,
        "until:",
        end.isoformat(),
        "charge:",
        charge_id
    )

    send_message(
        chat_id,
        "🎉 ОПЛАТА УСПЕШНА!\n\n"
        "⚡ VORTEX PREMIUM активирован.\n\n"
        "⭐ Оплачено: 65 Stars\n"
        "📅 Срок: 30 дней\n\n"
        f"Доступ действует до: "
        f"{end.strftime('%d.%m.%Y %H:%M')} UTC\n\n"
        "Теперь тебе доступны все функции VORTEX. 🚀",
        keyboard
    )


# =========================
# ПРОВЕРКА ДОСТУПА
# =========================

def check_access(chat_id):
    user = get_user(chat_id)

    paid_until = parse_date(user.get("paid_until"))
    trial_until = parse_date(user.get("trial_until"))

    now = datetime.now(timezone.utc)

    if paid_until and now < paid_until:
        return "paid"

    if trial_until:
        if now < trial_until:
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
        send_message(
            chat_id,
            "⏳ Твой доступ закончился.\n\n"
            "Оформи VORTEX PREMIUM, чтобы продолжить.",
            keyboard
        )
        payment_message(chat_id)
        return False

    return False


def count_request(chat_id):
    user = get_user(chat_id)

    if not user.get("trial_until") and not user.get("paid_until"):
        user["requests"] += 1
        save_users()


# =========================
# ИИ GROQ
# =========================

def ask_ai(chat_id, prompt):
    history = chat_histories.get(chat_id, [])

    messages = [
        {
            "role": "system",
            "content": (
                "Ты VORTEX, современный ИИ-помощник. "
                "Отвечай на русском языке. "
                "Общайся дружелюбно, уверенно и естественно. "
                "Не будь сухим и роботизированным. "
                "Пиши понятно, без лишней воды. "
                "Помогай создавать качественные посты, "
                "идеи для бизнеса и контента, работать с текстами "
                "и отвечать на вопросы. "
                "Используй эмодзи умеренно. "
                "Не выдумывай факты. "
                "Если не знаешь ответа, честно скажи об этом."
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
            "User-Agent": "VORTEX-Bot/1.0"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read())

        answer = result["choices"][0]["message"]["content"]

        history.append({
            "role": "user",
            "content": prompt
        })

        history.append({
            "role": "assistant",
            "content": answer
        })

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


# =========================
# УМНОЕ ПОНИМАНИЕ ЗАПРОСОВ ДЛЯ КАРТИНОК
# =========================

def prepare_image_prompt(user_idea):
    system_prompt = """
You are VORTEX, an expert AI image prompt engineer.

Convert the user's idea into a detailed English image-generation prompt.

The goal is to visualize exactly what the user imagines.

STRICT RULES:

1. The main object requested by the user MUST be the
   central and clearly visible subject of the image.

2. NEVER replace the requested object with scenery,
   sky, background or unrelated objects.

3. Preserve every explicitly requested detail:
   objects, brands, models, colors, locations, actions,
   camera angles, atmosphere and artistic style.

4. If the user gives a short idea, intelligently add
   suitable visual details, composition, lighting,
   environment and camera perspective.

5. Never contradict the user's idea.

6. Do not invent a specific car model, color, brand
   variant or other important detail if it was not specified.

7. Describe the main subject FIRST.
   Describe the environment SECOND.
   Describe lighting, camera and style THIRD.

8. The main subject must occupy a significant part
   of the image and be easy to recognize.

9. Avoid irrelevant scenery and empty backgrounds.

10. Do not add text, watermarks, captions or logos
    unless explicitly requested.

11. If the user requests a realistic image,
    use photorealistic photography language.

12. Return ONLY the final English prompt.
    No explanations, no introductions.
"""

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_idea
            }
        ],
        "temperature": 0.4,
        "max_completion_tokens": 500
    }

    req = urllib.request.Request(
        GROQ_API,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_KEY}",
            "User-Agent": "VORTEX-Bot/1.0"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read())

        improved = (
            result["choices"][0]["message"]["content"].strip()
        )

        if not improved:
            return user_idea

        print("Original image idea:", user_idea)
        print("Improved image prompt:", improved)

        return improved[:2048]

    except urllib.error.HTTPError as error:
        details = error.read().decode(errors="ignore")
        print("Image prompt Groq HTTP error:", error.code, details)
        return user_idea

    except Exception as error:
        print("Image prompt improvement error:", error)
        return user_idea


# =========================
# ГЕНЕРАЦИЯ КАРТИНОК CLOUDFLARE
# =========================

def generate_image(prompt):
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        print("Cloudflare credentials are missing")
        return None

    # Этап 1. Groq превращает задумку в подробный промпт
    original_prompt = prompt
    prompt = prepare_image_prompt(original_prompt)

    if len(prompt) > 2048:
        prompt = prompt[:2048]

    print("Final Flux prompt:", prompt)

    # Этап 2. Flux создаёт изображение
    payload = {
        "prompt": prompt,
        "steps": 4
    }

    req = urllib.request.Request(
        CF_IMAGE_API,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "User-Agent": "VORTEX-Bot/1.0"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read())

        if not result.get("success", False):
            print("Cloudflare API error:", result)
            return None

        image_base64 = result.get("result", {}).get("image")

        if not image_base64:
            print("Cloudflare returned no image")
            print(result)
            return None

        image_data = base64.b64decode(image_base64)

        if not image_data:
            print("Generated image is empty")
            return None

        return image_data

    except urllib.error.HTTPError as error:
        details = error.read().decode(errors="ignore")
        print("Cloudflare HTTP error:", error.code, details)
        return None

    except Exception as error:
        print("Cloudflare image error:", error)
        return None


# =========================
# РАСПОЗНАВАНИЕ ГОЛОСОВЫХ
# =========================

def transcribe_voice(file_id):
    try:
        file_info = tg_request("getFile", {
            "file_id": file_id
        })

        if not file_info.get("ok"):
            print("Telegram getFile error:", file_info)
            return None

        file_path = file_info["result"]["file_path"]

        download_url = (
            f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        )

        with urllib.request.urlopen(download_url, timeout=60) as response:
            audio_data = response.read()

        if not audio_data:
            print("Empty audio file")
            return None

        if len(audio_data) > 20 * 1024 * 1024:
            print("Audio file is too large")
            return None

        print(
            "Voice file:",
            file_path,
            "Size:",
            len(audio_data),
            "Signature:",
            audio_data[:4]
        )

        if audio_data[:4] != b"OggS":
            print("Audio is not an OGG file")
            return None

        boundary = "----VORTEX" + uuid.uuid4().hex

        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="model"\r\n\r\n'
            f"{WHISPER_MODEL}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="voice.ogg"\r\n'
            "Content-Type: audio/ogg\r\n\r\n"
        ).encode() + audio_data + (
            f"\r\n--{boundary}--\r\n"
        ).encode()

        req = urllib.request.Request(
            GROQ_AUDIO_API,
            data=body,
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "VORTEX-Bot/1.0"
            }
        )

        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read())

        recognized = result.get("text", "").strip()

        if not recognized:
            print("Whisper returned empty transcription")
            return None

        return recognized

    except urllib.error.HTTPError as error:
        details = error.read().decode(errors="ignore")
        print("Whisper HTTP error:", error.code, details)
        return None

    except Exception as error:
        print("Voice recognition error:", error)
        return None


# =========================
# CALLBACK-КНОПКИ
# =========================

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


# =========================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================

def handle_message(message):
    chat_id = message["chat"]["id"]

    if message.get("successful_payment"):
        activate_premium(message)
        return

    text = message.get("text", "").strip()
    is_voice = bool(message.get("voice"))

    # ГОЛОСОВЫЕ
    if is_voice:
        if not can_use_ai(chat_id):
            return

        send_message(chat_id, "🎙️ VORTEX слушает тебя...")

        recognized_text = transcribe_voice(
            message["voice"]["file_id"]
        )

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

    if not text:
        return

    # ПРИВЕТСТВИЕ
    if text == "/start":
        user_states.pop(chat_id, None)
        get_user(chat_id)

        send_message(
            chat_id,
            "⚡ VORTEX — ТВОЙ ЛИЧНЫЙ ИИ-ПОМОЩНИК\n\n"
            "Привет! Я VORTEX. Помогаю превращать "
            "идеи в готовые результаты.\n\n"
            "✍️ Создаю посты для соцсетей\n"
            "💡 Генерирую идеи для контента\n"
            "🎨 Создаю изображения по описанию\n"
            "🧠 Отвечаю на вопросы\n"
            "🎙️ Распознаю голосовые сообщения\n"
            "🔥 Улучшаю и редактирую тексты\n\n"
            "Выбирай функцию в меню или просто напиши мне.\n\n"
            "VORTEX. Твои идеи. Мои возможности. ⚡",
            keyboard
        )
        return

    # УСЛОВИЯ
    if text == "/terms":
        send_message(
            chat_id,
            "📄 Условия VORTEX PREMIUM\n\n"
            "1. Premium предоставляет доступ к функциям "
            "VORTEX на 30 дней.\n"
            "2. Стоимость одного периода: 65 Telegram Stars.\n"
            "3. Доступ активируется после подтверждения "
            "успешного платежа Telegram.\n"
            "4. Подписка не продлевается автоматически.\n"
            "5. Для продления необходимо совершить новый платёж.\n"
            "6. По вопросам оплаты используй команду /paysupport."
        )
        return

    # ПОДДЕРЖКА ОПЛАТЫ
    if text == "/paysupport":
        send_message(
            chat_id,
            "🛟 Поддержка VORTEX\n\n"
            "Если возникла проблема с оплатой, напиши "
            "владельцу бота через контакт, указанный "
            "в канале @VORTEX_RELAX.\n\n"
            "Сохрани чек Telegram, он поможет проверить платёж."
        )
        return

    # ОТМЕНА
    if text in ["/cancel", "❌ Отмена"]:
        user_states.pop(chat_id, None)
        send_message(chat_id, "Действие отменено.", keyboard)
        return

    # ПОКУПКА PREMIUM
    if text in ["/buy", "💎 Купить Premium"]:
        payment_message(chat_id)
        return

    # ВОЗМОЖНОСТИ
    if text == "✨ Возможности":
        send_message(
            chat_id,
            "✨ Возможности VORTEX:\n\n"
            "• Создание постов с помощью ИИ\n"
            "• Идеи для контента\n"
            "• Помощь с текстами\n"
            "• Ответы на вопросы\n"
            "• Разные стили написания\n"
            "• 🎙️ Распознавание голосовых\n"
            "• 🎨 Генерация изображений\n\n"
            "💎 Premium: 65 Stars за 30 дней.",
            keyboard
        )
        return

    # СОЗДАНИЕ ПОСТА
    if text == "✍️ Создать пост":
        user_states[chat_id] = {
            "step": "topic"
        }

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

    # ГЕНЕРАЦИЯ КАРТИНОК
    if text == "🎨 Создать картинку":
        user_states[chat_id] = {
            "step": "image_prompt"
        }

        send_message(
            chat_id,
            "🎨 VORTEX готов создать изображение!\n\n"
            "Опиши, что хочешь увидеть.\n\n"
            "Например:\n"
            "«Футуристический город ночью, "
            "неоновые вывески, дождь, кинематографичный стиль»\n\n"
            "Можешь писать даже коротко. Я сам дополню детали.",
            {
                "keyboard": [[{"text": "❌ Отмена"}]],
                "resize_keyboard": True
            }
        )
        return

    # ИДЕИ ДЛЯ КОНТЕНТА
    if text == "💡 Идеи для контента":
        user_states[chat_id] = {
            "step": "ideas"
        }

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

    # ПРОФИЛЬ
    if text == "👤 Мой профиль":
        user = get_user(chat_id)
        access = check_access(chat_id)

        if access == "free":
            status = (
                f"Бесплатные запросы: "
                f"{user['requests']}/{FREE_REQUESTS}"
            )

        elif access == "trial":
            trial_end = parse_date(user.get("trial_until"))
            status = (
                "Статус: бесплатный доступ\n"
                f"До: {trial_end.strftime('%d.%m.%Y')} UTC"
            )

        elif access == "paid":
            paid_end = parse_date(user.get("paid_until"))
            status = (
                "Статус: 💎 PREMIUM\n"
                f"До: {paid_end.strftime('%d.%m.%Y %H:%M')} UTC"
            )

        elif access == "expired":
            status = "Статус: доступ закончился"

        else:
            status = "Статус: требуется подписка на канал"

        send_message(
            chat_id,
            f"👤 Твой профиль\n"
            f"ID: {chat_id}\n\n"
            f"{status}\n\n"
            f"💎 Premium: {STARS_PRICE} Stars / 30 дней",
            keyboard
        )
        return

    state = user_states.get(chat_id, {})

    # ОПИСАНИЕ КАРТИНКИ
    if state.get("step") == "image_prompt":
        if not can_use_ai(chat_id):
            return

        if not CF_ACCOUNT_ID or not CF_API_TOKEN:
            send_message(
                chat_id,
                "❌ Генерация пока не настроена.\n"
                "Проверь переменные Cloudflare в Railway.",
                keyboard
            )
            return

        if len(text) > 2048:
            send_message(
                chat_id,
                "❌ Описание слишком длинное.\n"
                "Максимум 2048 символов. Сократи описание.",
                keyboard
            )
            return

        user_states.pop(chat_id, None)

        send_message(
            chat_id,
            "🎨 VORTEX анализирует твою задумку и создаёт изображение...\n\n"
            "Сначала я продумываю сцену, затем рисую её. Это займёт немного времени."
        )

        image_data = generate_image(text)

        if not image_data:
            send_message(
                chat_id,
                "❌ Не удалось создать изображение.\n\n"
                "Возможно, временно недоступен Cloudflare "
                "или закончилась дневная квота.\n"
                "Попробуй немного позже.",
                keyboard
            )
            return

        try:
            send_photo(
                chat_id,
                image_data,
                "⚡ Создано с помощью VORTEX"
            )

            count_request(chat_id)

        except Exception as error:
            print("Telegram sendPhoto error:", error)
            send_message(
                chat_id,
                "Изображение создано, но Telegram не смог "
                "его отправить. Попробуй ещё раз.",
                keyboard
            )

        return

    # ТЕМА ПОСТА
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

    # СТИЛЬ ПОСТА
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

    # ГЕНЕРАЦИЯ ИДЕЙ
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

    # ОБЫЧНЫЙ ЧАТ
    if not can_use_ai(chat_id):
        return

    send_message(chat_id, "⚡ Думаю над ответом...")

    answer = ask_ai(chat_id, text)

    count_request(chat_id)
    send_message(chat_id, answer, keyboard)


# =========================
# ЗАПУСК
# =========================

def main():
    offset = 0

    load_history()
    load_users()

    print("VORTEX запускается...")

    if CF_ACCOUNT_ID and CF_API_TOKEN:
        print("Cloudflare Image API настроен.")
    else:
        print("ВНИМАНИЕ: Cloudflare Image API не настроен.")

    try:
        tg_request("deleteWebhook", {
            "drop_pending_updates": "false"
        })
        print("Webhook проверен.")
    except Exception as error:
        print("Webhook check error:", error)

    print("VORTEX запущен и ожидает сообщения.")

    while True:
        try:
            updates = tg_request("getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": json.dumps([
                    "message",
                    "callback_query",
                    "pre_checkout_query"
                ])
            })

            for update in updates.get("result", []):
                offset = update["update_id"] + 1

                try:
                    if "pre_checkout_query" in update:
                        handle_pre_checkout(
                            update["pre_checkout_query"]
                        )

                    elif "callback_query" in update:
                        handle_callback(update["callback_query"])

                    elif "message" in update:
                        handle_message(update["message"])

                except Exception as error:
                    print("Update processing error:", error)

        except urllib.error.HTTPError as error:
            details = error.read().decode(errors="ignore")

            if error.code == 409:
                print(
                    "409 CONFLICT: другой процесс использует "
                    "этот BOT_TOKEN. Оставь только один экземпляр "
                    "VORTEX на Railway."
                )
                time.sleep(10)
            else:
                print("Telegram HTTP error:", error.code, details)
                time.sleep(5)

        except Exception as error:
            print("Ошибка:", error)
            time.sleep(5)


if __name__ == "__main__":
    main()
