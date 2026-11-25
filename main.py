import os
import sys
import json
import time
import uuid
import base64
import io
import re
import unicodedata
import subprocess
import tempfile
import wave
import urllib.request
import urllib.error
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageOps

TOKEN = os.environ["BOT_TOKEN"]
GROQ_KEY = os.environ["GROQ_API_KEY"]

CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")

TG_API = f"https://api.telegram.org/bot{TOKEN}/"
TG_FILE_API = f"https://api.telegram.org/file/bot{TOKEN}/"
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
GROQ_AUDIO_API = "https://api.groq.com/openai/v1/audio/transcriptions"

CF_IMAGE_API = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CF_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
)

CF_EDIT_API = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CF_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-2-klein-4b"
)

MODEL = "openai/gpt-oss-20b"
WHISPER_MODEL = "whisper-large-v3-turbo"
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

PIPER_VOICE = "ru_RU-ruslan-medium"
PIPER_DIR = os.path.join(os.getcwd(), "piper_voices")
piper_model = None

CHANNEL = "@VORTEX_RELAX"

FREE_REQUESTS = 5
TRIAL_DAYS = 7

STARS_PRICE = 65
PREMIUM_DAYS = 30

VOICE_MAX_TOKENS = 180
TEXT_MAX_TOKENS = 1200
VOICE_SPEED = "atempo=0.96"

user_states = {}
chat_histories = {}
users = {}
last_video_transcripts = {}

# =========================
# RAILWAY PERSISTENT STORAGE
# =========================

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
LAST_IMAGES_DIR = os.path.join(DATA_DIR, "last_images")
os.makedirs(LAST_IMAGES_DIR, exist_ok=True)

def last_image_path(chat_id):
    return os.path.join(LAST_IMAGES_DIR, f"{chat_id}.jpg")

def save_last_image(chat_id, image_data):
    """Сохраняет последнее созданное/отредактированное изображение чата."""
    path = last_image_path(chat_id)
    temp_path = path + ".tmp"
    with open(temp_path, "wb") as image_file:
        image_file.write(image_data)
    os.replace(temp_path, path)

def load_last_image(chat_id):
    path = last_image_path(chat_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as image_file:
        return image_file.read()

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

    # VORTEX работает без постоянной клавиатуры.
    # Сохраняем только inline-кнопки, например проверку подписки.
    if menu and menu.get("inline_keyboard"):
        data["reply_markup"] = json.dumps(menu, ensure_ascii=False)
    else:
        data["reply_markup"] = json.dumps({"remove_keyboard": True})

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


def send_document(chat_id, file_data, filename, caption=""):
    boundary = "----VORTEX" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        "Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        TG_API + "sendDocument", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "VORTEX-Bot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise Exception(str(result))
    return result


def send_voice(chat_id, audio_data, caption=""):
    boundary = "----VORTEX" + uuid.uuid4().hex

    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="voice"; filename="vortex.ogg"\r\n'
        "Content-Type: audio/ogg\r\n\r\n"
    ).encode() + audio_data + (
        f"\r\n--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        TG_API + "sendVoice",
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


def download_telegram_file(file_id):
    started = time.perf_counter()

    info = tg_request("getFile", {"file_id": file_id})

    if not info.get("ok"):
        raise Exception("Telegram не смог получить файл")

    file_path = info["result"]["file_path"]
    url = TG_FILE_API + file_path

    with urllib.request.urlopen(url, timeout=60) as response:
        file_data = response.read()

    print(
        f"[SPEED] Telegram download: "
        f"{time.perf_counter() - started:.2f} sec"
    )

    return file_data


# =========================
# PIPER: РУССКИЙ ГОЛОС
# =========================

def load_piper_model():
    global piper_model

    if piper_model is not None:
        return piper_model

    from piper import PiperVoice

    os.makedirs(PIPER_DIR, exist_ok=True)

    model_path = os.path.join(
        PIPER_DIR,
        PIPER_VOICE + ".onnx"
    )

    config_path = model_path + ".json"

    if not os.path.exists(model_path) or not os.path.exists(config_path):
        print("Downloading Piper voice:", PIPER_VOICE)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "piper.download_voices",
                PIPER_VOICE,
                "--download-dir",
                PIPER_DIR
            ],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            print("Piper download error:", result.stdout, result.stderr)
            raise Exception("Не удалось скачать голос Piper")

    if not os.path.exists(model_path):
        raise Exception("Файл модели Piper не найден")

    started = time.perf_counter()
    piper_model = PiperVoice.load(model_path)

    print("Piper voice loaded:", PIPER_VOICE)
    print(
        f"[SPEED] Piper model loading: "
        f"{time.perf_counter() - started:.2f} sec"
    )

    return piper_model


# =========================
# ОЧИСТКА ТЕКСТА ДЛЯ ОЗВУЧКИ
# =========================

def clean_text_for_voice(text):
    if not text:
        return ""

    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_#~]", " ", text)

    cleaned = []

    for char in text:
        category = unicodedata.category(char)

        if category.startswith(("L", "N", "M")):
            cleaned.append(char)
        elif char in ".,!?;:…-":
            cleaned.append(char)
        elif char.isspace():
            cleaned.append(" ")
        else:
            cleaned.append(" ")

    result = "".join(cleaned)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"\s+([.,!?;:…])", r"\1", result)

    return result.strip()


def text_to_speech(text):
    started = time.perf_counter()

    try:
        voice_text = clean_text_for_voice(text)

        if not voice_text:
            print("Piper: после очистки текст пустой")
            return None

        print("[VOICE] Cleaned text:", voice_text)

        voice = load_piper_model()

        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = os.path.join(temp_dir, "voice.wav")
            ogg_path = os.path.join(temp_dir, "voice.ogg")

            synthesis_started = time.perf_counter()

            with wave.open(wav_path, "wb") as wav_file:
                voice.synthesize_wav(voice_text, wav_file)

            print(
                f"[SPEED] Piper synthesis: "
                f"{time.perf_counter() - synthesis_started:.2f} sec"
            )

            ffmpeg_started = time.perf_counter()

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i", wav_path,
                    "-filter:a", VOICE_SPEED,
                    "-c:a", "libopus",
                    "-b:a", "48k",
                    "-ac", "1",
                    "-ar", "48000",
                    ogg_path
                ],
                check=True,
                capture_output=True,
                timeout=120
            )

            print(
                f"[SPEED] FFmpeg: "
                f"{time.perf_counter() - ffmpeg_started:.2f} sec"
            )

            with open(ogg_path, "rb") as audio:
                audio_data = audio.read()

            if audio_data:
                print("Piper audio generated:", len(audio_data), "bytes")
                print(
                    f"[SPEED] Piper total: "
                    f"{time.perf_counter() - started:.2f} sec"
                )
                return audio_data

        return None

    except Exception as error:
        print("Piper TTS error:", repr(error))
        return None


# =========================
# СОХРАНЕНИЕ ДАННЫХ
# =========================

def save_json(path, data):
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=DATA_DIR,
            delete=False
        ) as file:
            json.dump(data, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
            temp_path = file.name

        os.replace(temp_path, path)

    except Exception:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def load_json(path, default):
    legacy_path = os.path.join(
        os.getcwd(),
        os.path.basename(path)
    )

    if not os.path.exists(path):
        if os.path.exists(legacy_path):
            print("Migrating old data:", legacy_path)

            with open(legacy_path, "r", encoding="utf-8") as file:
                data = json.load(file)

            save_json(path, data)
            print("Data migrated:", path)
            return data

        print("No existing data file:", path)
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    except json.JSONDecodeError as error:
        backup_path = path + ".corrupt." + str(int(time.time()))
        os.replace(path, backup_path)

        print("CORRUPTED DATA BACKED UP:", backup_path)

        if os.path.exists(legacy_path):
            with open(legacy_path, "r", encoding="utf-8") as file:
                data = json.load(file)

            save_json(path, data)
            return data

        raise RuntimeError(
            "Data file is corrupted. Backup: " + backup_path
        ) from error


def save_history():
    save_json(HISTORY_FILE, chat_histories)


def load_history():
    global chat_histories

    data = load_json(HISTORY_FILE, {})

    chat_histories = {
        int(key): value
        for key, value in data.items()
    }


def save_users():
    save_json(USERS_FILE, users)


def load_users():
    global users

    users = load_json(USERS_FILE, {})


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
# ЧИСТЫЙ ТЕКСТ ДЛЯ ПОЛЬЗОВАТЕЛЯ
# =========================

def clean_ai_text(value):
    """Убирает Markdown-разметку, декоративные звёздочки и хештеги."""
    value = str(value or "")
    value = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", ""), value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"(?<!\w)\*{1,3}|\*{1,3}(?!\w)|(?<!\w)_{1,3}|_{1,3}(?!\w)", "", value)
    value = re.sub(r"(?m)^\s*[-•]\s+", "", value)
    value = re.sub(r"(?<!\w)#[\wА-Яа-яЁё]+", "", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


# =========================
# ИИ GROQ
# =========================

def ask_ai(chat_id, prompt, max_tokens=TEXT_MAX_TOKENS):
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
                "Выдавай только чистый обычный текст. "
                "Не используй Markdown, звёздочки для выделения, решётки, "
                "хештеги, декоративные разделители и форматирование. "
                "Не добавляй хештеги даже к рекламным постам. "
                "Не используй маркеры списков со звёздочками. "
                "Заголовки пиши обычными словами без символов вокруг. "
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
        "max_completion_tokens": max_tokens
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

    started = time.perf_counter()

    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read())

        answer = clean_ai_text(result["choices"][0]["message"]["content"])

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

        print(
            f"[SPEED] Groq: "
            f"{time.perf_counter() - started:.2f} sec"
        )

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
# УМНЫЙ ПРОМПТ ДЛЯ ГЕНЕРАЦИИ
# =========================

def prepare_image_prompt(user_idea):
    """Уточняет визуальный запрос, не меняя главный объект и условия пользователя."""
    system_prompt = """
You are an image prompt writer. Translate the user's request into a clear,
detailed English prompt for an image generator.

STRICT RULES:
- The exact subject named by the user is mandatory and must dominate the image.
- Never replace, omit, or reinterpret a named vehicle, person, animal, object,
  brand, or model. BMW means a BMW car, not a different car or a random scene.
- Preserve every explicit scene condition such as night, rain, city, color,
  location, camera angle, and style.
- Add only visual details that support the user's request.
- Put the requested main subject first and make it large, central, and clearly visible.
- Do not add unrelated objects, text, watermarks, logos, or extra subjects.
- Return only the final English image prompt.
"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                "Create a prompt for this exact request. Do not change its subject "
                "or conditions. ORIGINAL USER REQUEST: " + user_idea
            )}
        ],
        "temperature": 0.1,
        "max_completion_tokens": 350
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
        improved = result["choices"][0]["message"]["content"].strip()
        if improved:
            # Исходный запрос всегда остаётся в промпте как жёсткое ограничение.
            final_prompt = (
                improved[:1600]
                + "\n\nMANDATORY: Follow this original user request exactly: "
                + user_idea[:400]
                + ". The named subject and all stated conditions must be visible. "
                  "Do not substitute another subject or scene."
            )
            print("Original image idea:", user_idea)
            print("Improved image prompt:", final_prompt)
            return final_prompt[:2048]
    except Exception as error:
        print("Image prompt improvement error:", error)

    return (
        "Create an image showing exactly this scene: " + user_idea
        + ". The named subject must be central, large, and clearly visible. "
          "Do not replace it with anything else."
    )[:2048]


# =========================
# ОБЫЧНАЯ ГЕНЕРАЦИЯ КАРТИНОК
# =========================

def generate_image(prompt):
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        print("Cloudflare credentials are missing")
        return None

    prompt = prepare_image_prompt(prompt)

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
            print("Cloudflare generation error:", result)
            return None

        image_base64 = result.get("result", {}).get("image")

        if not image_base64:
            print("Cloudflare returned no image")
            print(result)
            return None

        return base64.b64decode(image_base64)

    except urllib.error.HTTPError as error:
        print(
            "Cloudflare generation HTTP error:",
            error.code,
            error.read().decode(errors="ignore")
        )
        return None

    except Exception as error:
        print("Cloudflare generation error:", error)
        return None


# =========================
# РЕДАКТИРОВАНИЕ ФОТО
# =========================

def prepare_edit_prompt(user_instruction):
    system_prompt = """
You are an expert image editing instruction translator.

Translate the user's image editing instruction into a clear,
detailed English instruction for an image editing AI.

The AI will receive the original image separately.

IMPORTANT:
- Edit the existing image, do not create an unrelated new scene.
- Preserve the original subject, identity, composition and perspective.
- Change only what the user explicitly requests.
- Keep all unrelated objects and details unchanged.
- For color changes, change the requested object's color only.
- Do not add text, watermarks or logos unless requested.
- Return only the final English editing instruction.
"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_instruction}
        ],
        "temperature": 0.3,
        "max_completion_tokens": 400
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

        prompt = result["choices"][0]["message"]["content"].strip()

        if prompt:
            return prompt[:1500]

    except Exception as error:
        print("Edit prompt error:", error)

    return (
        "Edit the provided image according to this instruction: "
        + user_instruction
    )


def get_cloudflare_edit_error(details):
    try:
        error_data = json.loads(details)
    except (json.JSONDecodeError, TypeError):
        return "cloudflare"

    errors = error_data.get("errors", [])

    for item in errors:
        if item.get("code") == 3030:
            return "blocked"

    return "cloudflare"


def edit_image(image_data, instruction):
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        print("Cloudflare credentials are missing")
        return None, "configuration"

    try:
        image = Image.open(io.BytesIO(image_data))
        image.load()

        print("Original image:", image.format, image.size)

        image = ImageOps.exif_transpose(image)

        if image.mode != "RGB":
            if image.mode in ("RGBA", "LA"):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(
                    rgba,
                    mask=rgba.getchannel("A")
                )
                image = background
            else:
                image = image.convert("RGB")

        image.thumbnail((511, 511), Image.Resampling.LANCZOS)

        width, height = image.size

        if width >= 512 or height >= 512:
            raise Exception(
                f"Размер изображения превышает лимит: {width}x{height}"
            )

        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=90,
            optimize=True
        )

        resized_data = output.getvalue()

        print(
            "Image resized:",
            width,
            "x",
            height,
            "bytes:",
            len(resized_data)
        )

        prompt = prepare_edit_prompt(instruction)

        boundary = "----VORTEX" + uuid.uuid4().hex

        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="prompt"\r\n\r\n'
            f"{prompt}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="input_image_0"; '
            'filename="input.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode() + resized_data + (
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="width"\r\n\r\n'
            "1024\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="height"\r\n\r\n'
            "1024\r\n"
            f"--{boundary}--\r\n"
        ).encode()

        req = urllib.request.Request(
            CF_EDIT_API,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {CF_API_TOKEN}",
                "User-Agent": "VORTEX-Bot/1.0"
            }
        )

        print("Starting image edit...")
        print("Edit instruction:", prompt)

        with urllib.request.urlopen(req, timeout=240) as response:
            result = json.loads(response.read())

        if not result.get("success", False):
            print("Cloudflare edit error:", result)

            for item in result.get("errors", []):
                if item.get("code") == 3030:
                    return None, "blocked"

            return None, "cloudflare"

        image_base64 = result.get("result", {}).get("image")

        if not image_base64:
            print("Cloudflare edit returned no image:", result)
            return None, "cloudflare"

        edited_data = base64.b64decode(image_base64)

        if not edited_data:
            print("Edited image is empty")
            return None, "cloudflare"

        return edited_data, None

    except urllib.error.HTTPError as error:
        details = error.read().decode(errors="ignore")
        print("Cloudflare edit HTTP error:", error.code, details)

        error_type = get_cloudflare_edit_error(details)

        return None, error_type

    except Exception as error:
        print("Cloudflare edit error:", error)
        return None, "technical"


# =========================
# ОБРАБОТКА ФОТО С ПОДПИСЬЮ
# =========================

def handle_photo_edit(message):
    chat_id = message["chat"]["id"]
    caption = message.get("caption", "").strip()

    if not caption:
        send_message(
            chat_id,
            "✍️ Напиши, что нужно изменить, прямо в подписи к фотографии.\n\n"
            "Например: «Сделай машину красной, сохрани фон и освещение»."
        )
        return

    if not can_use_ai(chat_id):
        return

    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        send_message(
            chat_id,
            "❌ Редактирование не настроено. Проверь Cloudflare в Railway.",
            keyboard
        )
        return

    if len(caption) > 1500:
        send_message(
            chat_id,
            "❌ Слишком длинная команда. Сократи её до 1500 символов."
        )
        return

    photos = message.get("photo", [])

    if not photos:
        send_message(chat_id, "❌ Не удалось получить фотографию.")
        return

    selected_photo = max(
        photos,
        key=lambda item: (
            item.get("width", 0) * item.get("height", 0)
        )
    )

    send_message(
        chat_id,
        "🎨 VORTEX получил фотографию и команду.\n\n"
        "Подготавливаю изображение и начинаю редактирование. "
        "Это может занять некоторое время."
    )

    try:
        image_data = download_telegram_file(selected_photo["file_id"])

        if not image_data:
            send_message(
                chat_id,
                "❌ Не удалось загрузить фотографию из Telegram."
            )
            return

        print(
            "Photo received:",
            selected_photo.get("width"),
            selected_photo.get("height"),
            "bytes:",
            len(image_data)
        )

        edited, error_type = edit_image(image_data, caption)

        if not edited:
            if error_type == "blocked":
                error_text = (
                    "⚠️ Cloudflare не разрешил обработать эту фотографию "
                    "с такой командой.\n\n"
                    "Попробуй другое фото или измени запрос. "
                    "Некоторые изображения и запросы могут блокироваться "
                    "защитными фильтрами сервиса."
                )

            elif error_type == "configuration":
                error_text = (
                    "❌ Не настроено подключение к Cloudflare. "
                    "Проверь переменные в Railway."
                )

            elif error_type == "cloudflare":
                error_text = (
                    "❌ Cloudflare не смог обработать изображение.\n\n"
                    "Попробуй ещё раз немного позже."
                )

            else:
                error_text = (
                    "❌ Произошла техническая ошибка при обработке фото.\n\n"
                    "Попробуй отправить изображение ещё раз."
                )

            send_message(chat_id, error_text, keyboard)
            return

        save_last_image(chat_id, edited)
        send_photo(
            chat_id,
            edited,
            "⚡ Фото отредактировано с помощью VORTEX"
        )

        count_request(chat_id)

    except Exception as error:
        print("Photo editing handler error:", error)
        send_message(
            chat_id,
            "❌ Произошла ошибка при обработке фотографии.\n"
            "Попробуй ещё раз.",
            keyboard
        )


# =========================
# TELEGRAM VIDEO NOTES (КРУЖКИ)
# =========================

def transcribe_audio_bytes(audio_data, filename="circle.mp3", content_type="audio/mpeg"):
    """Распознаёт аудиодорожку из видеокружка через Groq Whisper."""
    boundary = "----VORTEX" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{WHISPER_MODEL}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        GROQ_AUDIO_API, data=body,
        headers={"Authorization": f"Bearer {GROQ_KEY}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.loads(response.read())
    return result.get("text", "").strip()


def analyze_video_note(file_id, user_request=""):
    """Независимо извлекает кадры и звук из короткого Telegram-видеокружка."""
    if not file_id:
        raise ValueError("У видеокружка отсутствует file_id")

    video_data = download_telegram_file(file_id)
    if not video_data:
        raise ValueError("Telegram вернул пустой виде файл")
    if len(video_data) > 20 * 1024 * 1024:
        raise ValueError("Видеокружок превышает лимит 20 МБ")

    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = os.path.join(temp_dir, "circle.mp4")
        audio_path = os.path.join(temp_dir, "circle.mp3")
        with open(video_path, "wb") as f:
            f.write(video_data)

        # Сначала узнаём длительность и наличие потоков. Ошибка ffprobe
        # не должна мешать попытке извлечь кадры.
        duration = 0.0
        has_audio = False
        has_video = True
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type", "-of", "json", video_path],
                capture_output=True, text=True, timeout=25
            )
            if probe.returncode == 0 and probe.stdout.strip():
                probe_data = json.loads(probe.stdout)
                streams = probe_data.get("streams", [])
                has_audio = any(x.get("codec_type") == "audio" for x in streams)
                has_video = any(x.get("codec_type") == "video" for x in streams)
                raw_duration = probe_data.get("format", {}).get("duration")
                if raw_duration not in (None, "N/A", ""):
                    duration = max(0.0, float(raw_duration))
            else:
                print("[VIDEO NOTE] ffprobe failed:", (probe.stderr or "")[-1200:])
        except Exception as error:
            print("[VIDEO NOTE] ffprobe error:", repr(error))

        print(
            f"[VIDEO NOTE] duration={duration:.2f}s, bytes={len(video_data)}, "
            f"video={has_video}, audio={has_audio}"
        )

        # Аудио обрабатывается отдельно: отсутствие дорожки или ошибка
        # Whisper не обрывает визуальный анализ кружка.
        spoken_text = ""
        if has_audio:
            try:
                audio_result = subprocess.run(
                    ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1",
                     "-ar", "16000", "-b:a", "48k", audio_path],
                    capture_output=True, text=True, timeout=90
                )
                if audio_result.returncode == 0 and os.path.exists(audio_path):
                    with open(audio_path, "rb") as f:
                        audio_data = f.read()
                    if audio_data:
                        try:
                            spoken_text = transcribe_audio_bytes(
                                audio_data, filename="circle.mp3", content_type="audio/mpeg"
                            ) or ""
                            print("[VIDEO NOTE] speech recognized:", bool(spoken_text))
                        except Exception as error:
                            print("[VIDEO NOTE] Whisper error:", repr(error))
                else:
                    print("[VIDEO NOTE] audio extraction failed:", (audio_result.stderr or "")[-1200:])
            except Exception as error:
                print("[VIDEO NOTE] audio extraction error:", repr(error))
        else:
            print("[VIDEO NOTE] no audio stream; continuing with video frames")

        # Кадры извлекаются независимо от аудио. Для коротких кружков
        # используем относительные позиции внутри фактической длительности.
        frame_paths = []
        if has_video:
            if duration > 0.1:
                if duration <= 1.2:
                    seeks = [duration * 0.5]
                else:
                    seeks = [duration * 0.12, duration * 0.50, duration * 0.88]
            else:
                # Если ffprobe не смог определить длительность, пробуем
                # несколько безопасных позиций, начиная с самого начала.
                seeks = [0.0, 0.5, 1.0]

            for index, seek in enumerate(seeks):
                frame_path = os.path.join(temp_dir, f"frame_{index}.jpg")
                frame_result = subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{max(0.0, seek):.3f}",
                     "-i", video_path, "-frames:v", "1",
                     "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
                     "-q:v", "5", frame_path],
                    capture_output=True, text=True, timeout=45
                )
                if (frame_result.returncode == 0 and os.path.exists(frame_path)
                        and os.path.getsize(frame_path) > 0):
                    frame_paths.append(frame_path)
                else:
                    print(
                        f"[VIDEO NOTE] frame {index} failed:",
                        (frame_result.stderr or "")[-500:]
                    )

        if not frame_paths:
            raise RuntimeError(
                "Не удалось извлечь кадры из кружка. "
                "Проверьте формат видео и наличие видеодорожки."
            )

        content = [{"type": "text", "text": (
            "Проанализируй короткое видеосообщение Telegram (видеокружок). "
            "Оно может длиться всего 1-5 секунд: это нормально, не называй его слишком длинным "
            "и не отказывайся из-за длительности. Используй все переданные кадры и распознанную речь. "
            "Опиши только то, что действительно видно. Ответь на запрос пользователя. "
            "Если пользователь просит перевод, переведи распознанную речь полностью, "
            "сохраняя смысл и тон, без пересказа. Не выдумывай отсутствующие слова.\n"
            f"Распознанная речь: {spoken_text or 'Речь не распознана или аудио отсутствует.'}\n"
            f"Запрос пользователя: {user_request or 'Опиши, что происходит в кружке.'}"
        )}]

        for frame_path in frame_paths:
            with open(frame_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + encoded
            }})

        payload = {
            "model": VISION_MODEL,
            "messages": [
                {"role": "system", "content": (
                    "Ты VORTEX, спокойный и компетентный персональный ИИ-помощник. "
                    "Обращайся к пользователю 'сэр' естественно, не в каждом предложении. "
                    "Отвечай по-русски, если не просят другой язык. "
                    "Не используй Markdown, хештеги и декоративные символы. "
                    "Не придумывай детали, которых нет в видео или аудио. "
                    "Короткая длительность кружка не является причиной отказа: анализируй доступные кадры."
                )},
                {"role": "user", "content": content}
            ],
            "temperature": 0.2,
            "max_completion_tokens": 900
        }
        req = urllib.request.Request(
            GROQ_API, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {GROQ_KEY}",
                     "User-Agent": "VORTEX-Bot/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=150) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            details = error.read().decode(errors="ignore")
            print("[VIDEO NOTE] Vision HTTP error:", error.code, details[:3000])
            raise

        choices = result.get("choices") or []
        if not choices:
            print("[VIDEO NOTE] Vision returned no choices:", str(result)[:2000])
            raise RuntimeError("Vision API не вернул ответ")
        answer = (choices[0].get("message", {}).get("content") or "").strip()
        if not answer:
            raise RuntimeError("Vision API вернул пустой ответ")

        print(f"[VIDEO NOTE] success: frames={len(frame_paths)}, speech={bool(spoken_text)}")
        return answer, spoken_text


def handle_video_note(message):
    chat_id = message["chat"]["id"]
    if not can_use_ai(chat_id):
        return

    send_message(chat_id, "Сэр, анализирую видеокружок. Проверяю изображение и речь...")
    try:
        note = message.get("video_note") or {}
        file_id = note.get("file_id")
        if not file_id:
            raise ValueError("В сообщении отсутствует video_note.file_id")

        answer, spoken_text = analyze_video_note(
            file_id, message.get("caption", "")
        )
        if spoken_text:
            last_video_transcripts[chat_id] = spoken_text

        count_request(chat_id)
        send_message(chat_id, answer)

        # Сохраняем прежнее поведение озвучки перевода, если запрос передан в caption.
        if re.search(
            r"(?i)перевед|translate|translation|на английск|на немецк|на французск|на испанск",
            message.get("caption", "")
        ):
            audio_data = text_to_speech(answer)
            if audio_data:
                send_voice(chat_id, audio_data, "Перевод, сэр")

    except Exception as error:
        print("[VIDEO NOTE] Handler error:", repr(error))
        send_message(
            chat_id,
            "Сэр, не удалось обработать кружок. Попробуйте отправить его ещё раз."
        )


# =========================
# РАСПОЗНАВАНИЕ ГОЛОСОВЫХ
# =========================

def transcribe_voice(file_id):
    started = time.perf_counter()

    try:
        audio_data = download_telegram_file(file_id)

        if not audio_data:
            return None

        if len(audio_data) > 20 * 1024 * 1024:
            print("Audio file is too large")
            return None

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

        print(
            f"[SPEED] Whisper total: "
            f"{time.perf_counter() - started:.2f} sec"
        )

        return recognized or None

    except urllib.error.HTTPError as error:
        print(
            "Whisper HTTP error:",
            error.code,
            error.read().decode(errors="ignore")
        )
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

def is_image_edit_request(text):
    """Проверяет, просит ли пользователь изменить уже созданное фото."""
    value = text.lower().strip().replace("ё", "е")
    edit_words = (
        r"\b(?:измени|изменить|переделай|переделать|исправь|исправить|"
        r"отредактируй|отредактировать|добавь|добавить|убери|убрать|"
        r"замени|заменить|сделай\s+иначе|поменяй|поменять|"
        r"покрась|перекрась|перекрасить|сделай\s+фон)\b"
    )
    return bool(re.search(edit_words, value))


def handle_last_image_edit(chat_id, instruction):
    if not can_use_ai(chat_id):
        return True
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        send_message(chat_id, "Редактирование не настроено. Проверь Cloudflare в Railway.")
        return True
    original = load_last_image(chat_id)
    if not original:
        return False

    send_message(chat_id, "Понял. Изменяю последнее изображение, сохраняя остальное без изменений...")
    edited, error_type = edit_image(original, instruction)
    if not edited:
        if error_type == "blocked":
            send_message(chat_id, "Cloudflare не разрешил это изменение. Попробуй сформулировать запрос иначе.")
        elif error_type == "configuration":
            send_message(chat_id, "Не настроено подключение к Cloudflare. Проверь переменные Railway.")
        else:
            send_message(chat_id, "Не получилось изменить изображение. Попробуй ещё раз.")
        return True

    try:
        save_last_image(chat_id, edited)
        send_photo(chat_id, edited, "Готово. Это обновлённая версия изображения.")
        count_request(chat_id)
    except Exception as error:
        print("Last image edit send error:", error)
        send_message(chat_id, "Изменение готово, но Telegram не смог отправить фото.")
    return True


def build_presentation(chat_id, user_request):
    """Создаёт презентацию PPTX с текстом и иллюстрациями и возвращает bytes."""
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.enum.shapes import MSO_SHAPE
    except ImportError:
        print("python-pptx is not installed. Add python-pptx to requirements.txt")
        return None

    system = (
        "Ты создаёшь структуру презентации на русском языке. Верни ТОЛЬКО валидный JSON без Markdown. "
        "Формат: {\"title\":\"...\",\"subtitle\":\"...\",\"slides\":[{"
        "\"title\":\"...\",\"bullets\":[\"...\"],\"image_prompt\":\"...\"}]} . "
        "Сделай 6 содержательных слайдов после титульного. На каждом 2-4 коротких пункта. "
        "image_prompt на английском, конкретно описывает тематическую фотографию без текста и надписей. "
        "Не выдумывай статистику и факты. Если нужны цифры, избегай неподтверждённых точных значений."
    )
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "Подготовь презентацию по запросу: " + user_request}
        ],
        "temperature": 0.35,
        "max_completion_tokens": 2200
    }
    req = urllib.request.Request(GROQ_API, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {GROQ_KEY}", "User-Agent": "VORTEX-Bot/1.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read())
        raw = result["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        data = json.loads(raw)
        slides = data.get("slides", [])[:6]
        if not slides:
            raise ValueError("Модель не вернула слайды")
    except Exception as error:
        print("Presentation outline error:", error)
        return None

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    navy = RGBColor(17, 32, 58)
    blue = RGBColor(36, 105, 190)
    white = RGBColor(255, 255, 255)
    pale = RGBColor(239, 244, 250)

    def add_bg(slide, color):
        shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
        shape.fill.solid(); shape.fill.fore_color.rgb = color
        shape.line.fill.background()
        slide.shapes._spTree.remove(shape._element)
        slide.shapes._spTree.insert(2, shape._element)

    def add_text(slide, text, x, y, w, h, size, color, bold=False):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame; tf.clear(); tf.word_wrap = True
        tf.margin_left = Inches(0.05); tf.margin_right = Inches(0.05)
        p0 = tf.paragraphs[0]; p0.text = str(text)
        p0.font.name = "Aptos"; p0.font.size = Pt(size); p0.font.bold = bold; p0.font.color.rgb = color
        return box

    # Титульный слайд с тематической обложкой
    cover = prs.slides.add_slide(blank); add_bg(cover, navy)
    cover_prompt = "Professional cinematic editorial photograph for a presentation about " + str(data.get("title", user_request)) + ", wide landscape composition, no text, no letters, no watermark"
    cover_img = generate_image(cover_prompt)
    if cover_img:
        try:
            cover.shapes.add_picture(io.BytesIO(cover_img), Inches(7.0), Inches(0), width=Inches(6.333), height=Inches(7.5))
        except Exception as error: print("Cover image error:", error)
    add_text(cover, data.get("title", user_request), 0.75, 1.8, 6.0, 1.5,  thirty:=30, white, True)
    add_text(cover, data.get("subtitle", "Подготовлено VORTEX"), 0.8, 3.55, 5.7, 1.0, 19, RGBColor(205, 220, 240))
    add_text(cover, "VORTEX", 0.8, 6.65, 3.0, 0.4, 13, RGBColor(120, 170, 235), True)

    for index, item in enumerate(slides, start=1):
        slide = prs.slides.add_slide(blank); add_bg(slide, pale)
        # Верхняя синяя полоса
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, Inches(0.16))
        bar.fill.solid(); bar.fill.fore_color.rgb = blue; bar.line.fill.background()
        add_text(slide, item.get("title", f"Раздел {index}"), 0.65, 0.48, 12.0, 0.8, 26, navy, True)
        bullets = item.get("bullets", [])[:4]
        text_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.65), Inches(5.6), Inches(4.9))
        tf = text_box.text_frame; tf.clear(); tf.word_wrap = True
        for j, bullet in enumerate(bullets):
            para = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
            para.text = "•  " + str(bullet)
            para.font.name = "Aptos"; para.font.size = Pt(19); para.font.color.rgb = navy
            para.space_after = Pt(18)
        image_prompt = item.get("image_prompt") or ("High quality editorial photograph illustrating " + str(item.get("title", user_request)))
        image_bytes = generate_image(str(image_prompt) + ", realistic professional photography, landscape, no text, no watermark")
        if image_bytes:
            try:
                slide.shapes.add_picture(io.BytesIO(image_bytes), Inches(6.8), Inches(1.55), width=Inches(5.85), height=Inches(4.8))
            except Exception as error: print("Slide image error:", error)
        else:
            add_text(slide, "Иллюстрация временно недоступна", 7.0, 3.4, 5.2, 0.6, 16, blue)
        add_text(slide, f"{index:02d}", 12.15, 6.85, 0.5, 0.3, 11, blue, True)

    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()


def detect_intent(text):
    """Распознаёт намерение пользователя по обычной русской формулировке."""
    value = text.lower().strip().replace("ё", "е")

    presentation_patterns = (
        r"\b(?:презентац\w*|слайд\w*)\b",
        r"\b(?:сделай|создай|подготовь|составь)\b.*\b(?:powerpoint|pptx)\b"
    )
    if any(re.search(pattern, value) for pattern in presentation_patterns):
        return "presentation"

    # Не требуем одну конкретную фразу: «сделай мне фотку BMW»,
    # «хочу изображение…», «нарисуй…» должны вести в генератор.
    image_action = r"(?:нарис\w*|изобраз\w*|сгенериру\w*|созда\w*|сдела\w*|подготов\w*|сотвор\w*)"
    image_object = r"(?:картин\w*|изображен\w*|фото\w*|фотограф\w*|рисунок\w*|иллюстрац\w*)"
    image_patterns = (
        rf"\b{image_action}\b.*\b{image_object}\b",
        rf"\b{image_object}\b.*\b{image_action}\b",
        r"\b(?:нарис\w*|изобраз\w*|сгенериру\w*|созда\w*)\b",
        r"\b(?:хочу|нужна|нужно|сделай)\b.*\b(?:картин\w*|изображен\w*|фото\w*|фотограф\w*)\b",
        r"\b(?:генерац\w*|создан\w*)\b.*\b(?:картин\w*|изображен\w*|фото\w*)\b",
        # «Сделай BMW ночью», «сделай машину у моря» и похожие запросы.
        r"\b(?:сделай|создай|нарисуй|изобрази|сгенерируй)\b.*\b(?:bmw|бмв|mercedes|мерседес|audi|ауди|машин\w*|автомобил\w*|мотоцикл\w*|кот\w*|собак\w*|человек\w*|робот\w*)\b"
    )
    if any(re.search(pattern, value) for pattern in image_patterns):
        return "image"

    ideas_words = (
        "идеи для", "придумай идеи", "дай идеи", "идеи контента",
        "идеи постов", "контент-план", "контент план"
    )
    if any(word in value for word in ideas_words):
        return "ideas"

    post_words = (
        "напиши пост", "создай пост", "сделай пост", "пост для",
        "напиши реклам", "рекламный текст", "текст объявления",
        "описание товара", "напиши описание", "составь объявление"
    )
    if any(word in value for word in post_words):
        return "post"

    return "chat"


def handle_message(message):
    chat_id = message["chat"]["id"]

    if message.get("successful_payment"):
        activate_premium(message)
        return

    # Видеокружки обрабатываются отдельно: кадры + распознавание речи + перевод.
    if message.get("video_note"):
        handle_video_note(message)
        return

    # Фото с подписью автоматически направляется в редактор.
    if message.get("photo"):
        handle_photo_edit(message)
        return

    text = message.get("text", "").strip()
    is_voice = bool(message.get("voice"))

    if is_voice:
        total_started = time.perf_counter()
        if not can_use_ai(chat_id):
            return
        recognized_text = transcribe_voice(message["voice"]["file_id"])
        if not recognized_text:
            send_message(chat_id, "❌ Не удалось распознать голосовое. Попробуй отправить ещё раз.")
            return
        text = recognized_text
        print("[VOICE] Recognized:", text)

    if not text:
        return

    command = text.split()[0].split("@")[0].lower()

    # Перевод последнего кружка отдельной командой после его отправки.
    if (chat_id in last_video_transcripts and
            re.search(r"(?i)\b(?:переведи|перевод|translate)\b", text)):
        target_match = re.search(
            r"(?i)(?:на|to)\s+([а-яa-zё-]+(?:\s+[а-яa-zё-]+)?)", text
        )
        target_language = target_match.group(1).strip() if target_match else "английский"
        send_message(chat_id, f"Сэр, перевожу последний кружок на {target_language}...")
        translated = ask_ai(
            chat_id,
            "Переведи следующий распознанный текст полностью на " + target_language +
            ". Сохрани смысл, имена и тон. Выведи только перевод, без пояснений.\n\n" +
            last_video_transcripts[chat_id],
            max_tokens=TEXT_MAX_TOKENS
        )
        send_message(chat_id, translated)
        audio_data = text_to_speech(translated)
        if audio_data:
            send_voice(chat_id, audio_data, "Перевод последнего кружка, сэр")
        count_request(chat_id)
        return

    if command == "/start":
        user_states.pop(chat_id, None)
        get_user(chat_id)
        send_message(
            chat_id,
            "⚡ VORTEX на связи.\n\n"
            "Просто напиши, что нужно сделать, или отправь голосовое.\n\n"
            "Я могу создать пост, придумать идеи, ответить на вопрос, "
            "сгенерировать изображение, отредактировать фото, создать презентацию PowerPoint "
            "или проанализировать и перевести видеокружок.\n\n"
            "Голосовое сообщение → голосовой ответ.\n"
            "Текст → текстовый ответ.\n\n"
            "Команды: /profile, /buy, /terms, /paysupport, /cancel\n\n"
            "VORTEX. Твои идеи. Мои возможности. ⚡"
        )
        return

    if command == "/terms":
        send_message(chat_id,
            "📄 Условия VORTEX PREMIUM\n\n"
            "1. Premium предоставляет доступ к функциям VORTEX на 30 дней.\n"
            "2. Стоимость: 65 Telegram Stars.\n"
            "3. Доступ активируется после подтверждения успешного платежа Telegram.\n"
            "4. Подписка не продлевается автоматически.\n"
            "5. Для продления необходимо совершить новый платёж.\n"
            "6. По вопросам оплаты используй /paysupport.")
        return

    if command == "/paysupport":
        send_message(chat_id,
            "🛟 Поддержка VORTEX\n\n"
            "Если возникла проблема с оплатой, напиши владельцу бота "
            "через контакт, указанный в канале @VORTEX_RELAX.\n\n"
            "Сохрани чек Telegram, он поможет проверить платёж.")
        return

    if command == "/cancel":
        user_states.pop(chat_id, None)
        send_message(chat_id, "Хорошо, отменил последнее действие. Можем начать заново.")
        return

    if command in ("/buy", "/premium"):
        payment_message(chat_id)
        return

    if command in ("/profile", "/me"):
        user = get_user(chat_id)
        access = check_access(chat_id)
        if access == "free":
            status = f"Бесплатные запросы: {user['requests']}/{FREE_REQUESTS}"
        elif access == "trial":
            trial_end = parse_date(user.get("trial_until"))
            status = f"Статус: бесплатный доступ до {trial_end.strftime('%d.%m.%Y')} UTC"
        elif access == "paid":
            paid_end = parse_date(user.get("paid_until"))
            status = f"Статус: 💎 PREMIUM до {paid_end.strftime('%d.%m.%Y %H:%M')} UTC"
        elif access == "expired":
            status = "Статус: доступ закончился"
        else:
            status = "Статус: требуется подписка на канал"
        send_message(chat_id,
            f"👤 Твой профиль\nID: {chat_id}\n\n{status}\n\n"
            f"💎 Premium: {STARS_PRICE} Stars / 30 дней")
        return

    # Совместимость со старыми текстовыми командами меню.
    lowered = text.lower()
    if text == "💎 Купить Premium":
        payment_message(chat_id)
        return
    if text == "👤 Мой профиль":
        user = get_user(chat_id)
        access = check_access(chat_id)
        status = f"Запросы: {user['requests']}/{FREE_REQUESTS}" if access == "free" else f"Статус доступа: {access}"
        send_message(chat_id, f"👤 Твой профиль\n\n{status}\n💎 Premium: {STARS_PRICE} Stars / 30 дней")
        return
    if text == "✨ Возможности":
        send_message(chat_id,
            "Я умею создавать посты, придумывать идеи, отвечать на вопросы, "
            "генерировать изображения, редактировать фотографии и создавать презентации PowerPoint. "
            "Просто напиши задачу обычными словами.")
        return

    # Если пользователь просит изменить последнее созданное фото, используем его без повторной отправки.
    if is_image_edit_request(text) and load_last_image(chat_id):
        if handle_last_image_edit(chat_id, text):
            return

    # Старые незавершённые сценарии не должны перехватывать новые сообщения.
    user_states.pop(chat_id, None)

    if not can_use_ai(chat_id):
        return

    intent = "chat" if is_voice else detect_intent(text)

    if intent == "presentation":
        send_message(chat_id, "Понял. Собираю презентацию: готовлю содержание, иллюстрации и оформляю слайды. Это может занять несколько минут.")
        presentation_data = build_presentation(chat_id, text)
        if not presentation_data:
            send_message(chat_id, "Не получилось собрать презентацию. Проверь, что в Railway установлена библиотека python-pptx, а Cloudflare и Groq доступны.")
            return
        try:
            send_document(chat_id, presentation_data, "VORTEX_presentation.pptx", "Готово. Презентация PowerPoint с иллюстрациями.")
            count_request(chat_id)
        except Exception as error:
            print("Presentation send error:", error)
            send_message(chat_id, "Презентация создана, но Telegram не смог отправить файл.")
        return

    if intent == "image":
        if not CF_ACCOUNT_ID or not CF_API_TOKEN:
            send_message(chat_id, "❌ Генерация пока не настроена. Проверь переменные Cloudflare в Railway.")
            return
        if len(text) > 2048:
            send_message(chat_id, "❌ Описание слишком длинное. Максимум 2048 символов.")
            return
        prompt = re.sub(
            r"(?i)^\s*(?:пожалуйста[, ]*)?(?:нарисуй(?:\s+мне)?|нарисовать|изобрази(?:\s+мне)?|"
            r"создай(?:\s+мне)?(?:\s+(?:картинку|изображение|фото))?|"
            r"сгенерируй(?:\s+мне)?(?:\s+(?:картинку|изображение|фото))?|"
            r"сделай(?:\s+мне)?(?:\s+(?:картинку|изображение|фото))?|"
            r"хочу(?:\s+)?(?:картинку|изображение|фото))\s*[:,\-]?\s*",
            "", text
        ).strip() or text
        send_message(chat_id, "🎨 Понял. Продумываю сцену и создаю изображение...")
        image_data = generate_image(prompt)
        if not image_data:
            send_message(chat_id, "❌ Не удалось создать изображение. Возможно, временно недоступен Cloudflare или закончилась квота. Попробуй позже.")
            return
        try:
            save_last_image(chat_id, image_data)
            send_photo(chat_id, image_data, "Создано с помощью VORTEX")
            count_request(chat_id)
        except Exception as error:
            print("Telegram sendPhoto error:", error)
            send_message(chat_id, "Изображение создано, но Telegram не смог его отправить. Попробуй ещё раз.")
        return

    if intent == "post":
        send_message(chat_id, "✍️ Понял. Подготовлю готовый пост...")
        prompt = (
            "Создай готовый качественный пост по запросу пользователя. "
            "Если стиль не указан, выбери подходящий сам. Добавь сильный заголовок, "
            "основной текст и уместный призыв к действию. Выводи только чистый текст: "
            "без Markdown, звёздочек, решёток, хештегов и декоративных символов. "
            "Не задавай вопросы, если данных достаточно. Запрос: " + text
        )
        answer = ask_ai(chat_id, prompt, max_tokens=TEXT_MAX_TOKENS)
        count_request(chat_id)
        send_message(chat_id, answer)
        return

    if intent == "ideas":
        send_message(chat_id, "💡 Понял. Собираю идеи...")
        answer = ask_ai(
            chat_id,
            "Придумай 10 конкретных и разнообразных идей для контента "
            "по запросу пользователя. Для каждой дай короткое описание. "
            "Оформи обычным текстом, без Markdown, звёздочек, решёток и хештегов. Запрос: " + text,
            max_tokens=TEXT_MAX_TOKENS
        )
        count_request(chat_id)
        send_message(chat_id, answer)
        return

    # Обычный разговор с ИИ.
    if not is_voice:
        send_message(chat_id, "⚡ Думаю...")

    if is_voice:
        voice_prompt = (
            "Ответь кратко и естественно, максимум 2–4 предложения. "
            "Ты персональный ИИ-помощник в духе высокотехнологичного дворецкого: "
            "спокойный, уверенный, точный, с лёгкой сухой иронией по ситуации. "
            "Обращайся к пользователю 'сэр' уместно, но не в каждом предложении. "
            "Не изображай конкретного кино-персонажа и не копируй чужой голос. "
            "Не используй списки, Markdown и эмодзи. Отвечай так, чтобы "
            "это звучало естественно голосом. Вопрос пользователя: " + text
        )
        answer = ask_ai(chat_id, voice_prompt, max_tokens=VOICE_MAX_TOKENS)
    else:
        answer = ask_ai(chat_id, text, max_tokens=TEXT_MAX_TOKENS)

    count_request(chat_id)

    if is_voice:
        audio_data = text_to_speech(answer)
        if audio_data:
            try:
                send_started = time.perf_counter()
                send_voice(chat_id, audio_data)
                print(f"[SPEED] Telegram sendVoice: {time.perf_counter() - send_started:.2f} sec")
            except Exception as error:
                print("Voice sending error:", error)
                send_message(chat_id, "❌ Не получилось отправить голосовой ответ. Попробуй ещё раз.")
        else:
            send_message(chat_id, "❌ Не получилось создать голосовой ответ. Попробуй ещё раз.")
        print(f"[SPEED] TOTAL voice: {time.perf_counter() - total_started:.2f} sec")
    else:
        send_message(chat_id, answer)


# =========================
# ЗАПУСК
# =========================

def main():
    offset = 0

    load_history()
    load_users()

    print("VORTEX запускается...")
    print("Persistent storage directory:", DATA_DIR)
    print("Users file:", USERS_FILE, "exists:", os.path.exists(USERS_FILE))
    print("History file:", HISTORY_FILE, "exists:", os.path.exists(HISTORY_FILE))

    if CF_ACCOUNT_ID and CF_API_TOKEN:
        print("Cloudflare Image API настроен.")
        print("Cloudflare Image Editing API настроен.")
    else:
        print("ВНИМАНИЕ: Cloudflare API не настроен.")

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
