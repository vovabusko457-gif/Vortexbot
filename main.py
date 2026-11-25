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

PIPER_VOICE = "ru_RU-dmitri-medium"
PIPER_DIR = os.path.join(os.getcwd(), "piper_voices")
piper_model = None

CHANNEL = "@VORTEX_RELAX"

FREE_REQUESTS = 5
TRIAL_DAYS = 7

STARS_PRICE = 65
PREMIUM_DAYS = 30

VOICE_MAX_TOKENS = 180
TEXT_MAX_TOKENS = 1200
VOICE_SPEED = "atempo=1.10"

user_states = {}
chat_histories = {}
users = {}

# =========================
# RAILWAY PERSISTENT STORAGE
# =========================

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

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
    system_prompt = """
You are VORTEX, an expert AI image prompt engineer.

Convert the user's idea into a detailed English image-generation prompt.

The main object requested by the user MUST be the central
and clearly visible subject.

Preserve all requested objects, brands, models, colors,
locations, actions, camera angles and artistic styles.

Never replace the requested object with scenery.
Do not invent important details.
Describe the main subject first, then environment,
lighting, camera and style.

Do not add text, watermarks or logos unless requested.
Return ONLY the final English prompt.
"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_idea}
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

        improved = result["choices"][0]["message"]["content"].strip()

        if improved:
            print("Original image idea:", user_idea)
            print("Improved image prompt:", improved)
            return improved[:2048]

    except Exception as error:
        print("Image prompt improvement error:", error)

    return user_idea


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

def detect_intent(text):
    """Простая маршрутизация естественных запросов без кнопок."""
    value = text.lower().strip()

    image_words = (
        "нарисуй", "создай картинку", "создай изображение",
        "сгенерируй картинку", "сгенерируй изображение",
        "сделай картинку", "сделай изображение", "изобрази",
        "нарисовать", "генерация изображения", "сгенерируй фото"
    )
    if any(word in value for word in image_words):
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

    if command == "/start":
        user_states.pop(chat_id, None)
        get_user(chat_id)
        send_message(
            chat_id,
            "⚡ VORTEX на связи.\n\n"
            "Просто напиши, что нужно сделать, или отправь голосовое.\n\n"
            "Я могу создать пост, придумать идеи, ответить на вопрос, "
            "сгенерировать изображение или отредактировать присланное фото.\n\n"
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
            "генерировать изображения и редактировать фотографии. "
            "Просто напиши задачу обычными словами.")
        return

    # Старые незавершённые сценарии не должны перехватывать новые сообщения.
    user_states.pop(chat_id, None)

    if not can_use_ai(chat_id):
        return

    intent = "chat" if is_voice else detect_intent(text)

    if intent == "image":
        if not CF_ACCOUNT_ID or not CF_API_TOKEN:
            send_message(chat_id, "❌ Генерация пока не настроена. Проверь переменные Cloudflare в Railway.")
            return
        if len(text) > 2048:
            send_message(chat_id, "❌ Описание слишком длинное. Максимум 2048 символов.")
            return
        prompt = re.sub(
            r"(?i)^\s*(нарисуй|нарисовать|изобрази|создай картинку|создай изображение|сгенерируй картинку|сгенерируй изображение|сделай картинку|сделай изображение|сгенерируй фото)\s*[:,\-]?\s*",
            "", text
        ).strip() or text
        send_message(chat_id, "🎨 Понял. Продумываю сцену и создаю изображение...")
        image_data = generate_image(prompt)
        if not image_data:
            send_message(chat_id, "❌ Не удалось создать изображение. Возможно, временно недоступен Cloudflare или закончилась квота. Попробуй позже.")
            return
        try:
            send_photo(chat_id, image_data, "⚡ Создано с помощью VORTEX")
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
            "основной текст и уместный призыв к действию. Не задавай вопросы, "
            "если данных достаточно. Запрос: " + text
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
            "по запросу пользователя. Для каждой дай короткое описание. Запрос: " + text,
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
