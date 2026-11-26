import os
import sys
import json
import time
import uuid
import base64
import io
import re
import random
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
VISION_MODEL = "qwen/qwen3.8-27b"

PIPER_VOICE = "ru_RU-ruslan-medium"
PIPER_DIR = os.path.join(os.getcwd(), "piper_voices")
piper_model = None

# Основной голос VORTEX: TeraTTSv2. Piper остаётся резервным.
TERATTS_MODEL_ID = "TeraSpace/TeraTTSv2"
TERATTS_REVISION = "75983ac"
TERATTS_VOICE = "ru_m5"
TERATTS_DURATION_SCALE = 1.05
teratts_model = None
teratts_loading = False
teratts_failed = False

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
long_term_memories = {}

# =========================
# RAILWAY PERSISTENT STORAGE
# =========================

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
MEMORY_FILE = os.path.join(DATA_DIR, "memories.json")
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
    """Скачивает файл Telegram с безопасной диагностикой и повторами временных ошибок."""
    started = time.perf_counter()

    try:
        info = tg_request("getFile", {"file_id": file_id})
    except urllib.error.HTTPError as error:
        details = error.read(1000).decode("utf-8", errors="replace")
        print(f"[VIDEO DOWNLOAD] getFile HTTP {error.code}: {details[:500]}")
        raise RuntimeError(f"Telegram getFile вернул HTTP {error.code}") from error
    except Exception as error:
        print("[VIDEO DOWNLOAD] getFile request failed:", repr(error))
        raise

    if not info.get("ok"):
        print("[VIDEO DOWNLOAD] getFile API error:", str(info)[:500])
        raise RuntimeError("Telegram getFile вернул ok=false")

    result = info.get("result") or {}
    file_path = result.get("file_path")
    file_size = result.get("file_size")
    if not file_path:
        raise RuntimeError("Telegram getFile не вернул file_path")

    # Облачный Bot API ограничивает скачивание файлов размером 20 МБ.
    max_size = 20 * 1024 * 1024
    if file_size is not None and int(file_size) > max_size:
        raise RuntimeError(
            f"Видеокружок слишком большой: {int(file_size)} байт, лимит облачного Bot API 20 МБ"
        )

    url = TG_FILE_API + file_path
    last_error = None
    for attempt in range(1, 4):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "VORTEX-Bot/1.0",
                "Connection": "close"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                file_data = response.read(max_size + 1)
                content_type = response.headers.get("Content-Type", "unknown")

            if not file_data:
                raise RuntimeError("Telegram вернул пустой файл")
            if len(file_data) > max_size:
                raise RuntimeError("Видеокружок превышает лимит скачивания 20 МБ")

            print(
                f"[SPEED] Telegram download: {time.perf_counter() - started:.2f} sec; "
                f"bytes={len(file_data)}; content_type={content_type}; attempt={attempt}"
            )
            return file_data

        except urllib.error.HTTPError as error:
            details = error.read(1000).decode("utf-8", errors="replace")
            print(
                f"[VIDEO DOWNLOAD] file download HTTP {error.code}; "
                f"attempt={attempt}; response={details[:500]}"
            )
            last_error = error
            # 403 обычно не является временной ошибкой. Не повторяем его вслепую.
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise RuntimeError(
                    f"Telegram file download вернул HTTP {error.code}: {details[:300]}"
                ) from error
        except (TimeoutError, OSError, urllib.error.URLError) as error:
            print(f"[VIDEO DOWNLOAD] network error attempt={attempt}: {repr(error)}")
            last_error = error
            if attempt == 3:
                raise RuntimeError("Сетевая ошибка при скачивании видеокружка") from error

        time.sleep(attempt * 1.5)

    raise RuntimeError("Не удалось скачать видеокружок") from last_error


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


def piper_text_to_speech(text):
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


def load_teratts_model():
    """Ленивая загрузка TeraTTSv2: модель скачивается только при первом голосовом ответе."""
    global teratts_model, teratts_loading, teratts_failed

    if teratts_model is not None:
        return teratts_model
    if teratts_failed:
        raise RuntimeError("TeraTTSv2 ранее не удалось загрузить")
    if teratts_loading:
        raise RuntimeError("TeraTTSv2 уже загружается")

    teratts_loading = True
    started = time.perf_counter()
    try:
        from transformers import AutoModel
        print("[TERATTS] Loading:", TERATTS_MODEL_ID, "revision:", TERATTS_REVISION)
        teratts_model = AutoModel.from_pretrained(
            TERATTS_MODEL_ID,
            revision=TERATTS_REVISION,
            trust_remote_code=True,
            provider="CPUExecutionProvider",
            threads=4,
            ruaccent_mode="dictionary"
        )
        print(f"[SPEED] TeraTTSv2 loading: {time.perf_counter() - started:.2f} sec")
        print("[TERATTS] Model loaded successfully")
        return teratts_model
    except Exception as error:
        teratts_failed = True
        print("[TERATTS] Model loading failed:", repr(error))
        raise
    finally:
        teratts_loading = False


def teratts_text_to_speech(text):
    clean = clean_text_for_voice(text)
    if not clean:
        return None

    model = load_teratts_model()
    with tempfile.TemporaryDirectory() as temp_dir:
        wav_path = os.path.join(temp_dir, "vortex_teratts.wav")
        ogg_path = os.path.join(temp_dir, "vortex_teratts.ogg")
        started = time.perf_counter()

        model.save_wav(
            wav_path,
            model.generate_speech(
                f"<ru>{clean}</ru>",
                voice=TERATTS_VOICE,
                duration_scale=TERATTS_DURATION_SCALE
            )
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libopus",
             "-b:a", "48k", "-application", "voip", "-ac", "1", ogg_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=180
        )
        with open(ogg_path, "rb") as audio_file:
            audio_data = audio_file.read()
        if not audio_data:
            raise RuntimeError("TeraTTSv2 вернул пустой аудиофайл")
        print(f"[SPEED] TeraTTSv2 synthesis + encode: {time.perf_counter() - started:.2f} sec")
        return audio_data


def text_to_speech(text):
    """Сначала новый голос. При обычной ошибке переключается на Piper."""
    try:
        return teratts_text_to_speech(text)
    except Exception as error:
        print("[TERATTS] Voice generation failed, falling back to Piper:", repr(error))
        return piper_text_to_speech(text)


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


def save_memories():
    save_json(MEMORY_FILE, long_term_memories)


def load_memories():
    global long_term_memories
    data = load_json(MEMORY_FILE, {})
    long_term_memories = {str(key): value for key, value in data.items() if isinstance(value, dict)}


def get_memory_summary(chat_id):
    entry = long_term_memories.get(str(chat_id), {})
    return str(entry.get("summary", "")).strip()


def update_long_term_memory(chat_id, user_text, assistant_text, event_type="dialogue"):
    """Накапливает важные события из диалогов и специальных функций."""
    key = str(chat_id)
    entry = long_term_memories.setdefault(
        key, {"summary": "", "pending_turns": 0, "events": []}
    )
    entry.setdefault("events", [])
    entry["pending_turns"] = int(entry.get("pending_turns", 0)) + 1

    # Храним небольшой буфер событий, чтобы не терять проекты между
    # специальными сценариями, которые не попадают в обычную историю чата.
    event = {
        "type": str(event_type)[:40],
        "user": str(user_text or "")[:700],
        "result": str(assistant_text or "")[:500],
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d")
    }
    entry["events"] = (entry["events"] + [event])[-12:]

    # Не вызываем дополнительную модель после каждого сообщения.
    if entry["pending_turns"] < 4:
        save_memories()
        return

    recent = chat_histories.get(chat_id, [])[-8:]
    old_summary = str(entry.get("summary", "")).strip()
    recent_events = entry.get("events", [])[-12:]
    prompt = (
        "Обнови краткую долговременную память о пользователе для будущих разговоров. "
        "Сохраняй полезные устойчивые факты, предпочтения, текущие проекты, цели, принятые решения "
        "и важный контекст. Учитывай события не только из обычного чата, но и из специальных функций: "
        "создание и редактирование изображений, презентаций, постов, идей и анализ видео. "
        "Сохраняй проект или задачу, если по событию видно, что пользователь над ней работает, "
        "но не превращай память в журнал всех действий. Объединяй повторяющиеся сведения, "
        "обновляй устаревшие факты по более новым сообщениям и отмечай незавершённые дела. "
        "Не сохраняй случайные мелочи, одноразовые вопросы, пароли, ключи, токены, коды, "
        "финансовые реквизиты или другие секреты. Не делай выводов, которых пользователь прямо не сообщал. "
        "Пиши по-русски, максимум 1600 символов. Верни только обновлённую память обычным текстом. "
        "Если сохранять нечего, верни пустую строку.\n\n"
        f"Предыдущая память:\n{old_summary or 'Пока пусто.'}\n\n"
        f"События с момента прошлого обновления (JSON):\n{json.dumps(recent_events, ensure_ascii=False)}\n\n"
        f"Последние сообщения обычного диалога (JSON):\n{json.dumps(recent, ensure_ascii=False)}"
    )
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Ты аккуратный редактор персональной памяти. Не выдумывай факты и не сохраняй секреты."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_completion_tokens": 600
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
        summary = clean_ai_text(result["choices"][0]["message"]["content"])[:1600]
        entry["summary"] = summary
        entry["pending_turns"] = 0
        entry["events"] = []
        save_memories()
        print(f"[MEMORY] Updated for chat {chat_id}; chars={len(summary)}")
    except Exception as error:
        # События и счётчик остаются на месте, чтобы повторить обновление позже.
        print("[MEMORY] Update failed:", repr(error))
        save_memories()


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

    memory_summary = get_memory_summary(chat_id)
    memory_context = (
        "\n\nДолговременная память о пользователе (используй только если это уместно):\n" + memory_summary
        if memory_summary else ""
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Ты VORTEX, персональный ИИ-собеседник пользователя. "
                "Говори по-русски живо, уверенно и естественно, как остроумный знакомый с хорошим чувством юмора. "
                "Обращайся к пользователю 'сэр' регулярно и к месту: это часть твоего фирменного стиля, "
                "но не начинай механически каждое сообщение с этого слова. "
                "Будь сообразительным, слегка дерзким и саркастичным, допускай лёгкий мат, "
                "если он органично подходит к разговору. Не будь грубым и не унижай пользователя. "
                "Не отвечай канцеляритом и не превращай простой разговор в лекцию. "
                "Сначала реагируй на смысл сказанного, затем при необходимости добавляй короткую шутку "
                "или встречный вопрос. Не используй одни и те же заготовленные шутки. "
                "Поддерживай ощущение непрерывного знакомства: если в долговременной памяти есть подходящая "
                "незавершённая тема, проект или общая шутка, иногда естественно возвращайся к ней сам, "
                "но только когда это уместно текущему разговору. Не притягивай старые темы к каждому ответу, "
                "не делай вид, будто помнишь то, чего нет в памяти, и не перебивай этим прямой ответ на вопрос. "
                "Если пользователь давно не возвращался к важному незавершённому делу, можно ненавязчиво спросить, "
                "как оно продвинулось. Не инициируй такие напоминания в каждом диалоге. "
                "Если пользователь делится чем-то или что-то показывает, реагируй как собеседник, "
                "а не как диктор, составляющий описание предмета. "
                "Для серьёзных вопросов сохраняй точность и отвечай по существу. Не выдумывай факты. "
                "Выдавай чистый обычный текст без Markdown, хештегов и декоративного форматирования. "
                "Если не знаешь ответа, честно скажи об этом."
            ) + memory_context
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

        update_long_term_memory(chat_id, caption, "VORTEX успешно отредактировал фотографию по инструкции пользователя.", "photo_edit")
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
                 "Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": "VORTEX-Bot/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        details = error.read(1500).decode("utf-8", errors="replace")
        print(
            f"[VIDEO WHISPER] Groq HTTP {error.code}; "
            f"response={details[:1000]}"
        )
        raise RuntimeError(
            f"Groq Whisper вернул HTTP {error.code}: {details[:300]}"
        ) from error
    except (TimeoutError, OSError, urllib.error.URLError) as error:
        print("[VIDEO WHISPER] Groq network error:", repr(error))
        raise RuntimeError("Сетевая ошибка при распознавании аудио кружка") from error
    return result.get("text", "").strip()


def analyze_video_note(file_id, user_request="", chat_id=None):
    """Полностью извлекает аудио и анализирует видеокружок по кадрам по всей длительности."""
    video_data = download_telegram_file(file_id)
    # Облачный Telegram Bot API обычно ограничивает скачивание файлов ботом 20 МБ.
    if not video_data or len(video_data) > 20 * 1024 * 1024:
        raise Exception("Кружок пустой или превышает доступный Telegram Bot API лимит скачивания 20 МБ")

    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = os.path.join(temp_dir, "circle.mp4")
        audio_path = os.path.join(temp_dir, "circle.mp3")
        with open(video_path, "wb") as f:
            f.write(video_data)

        subprocess.run([
            "ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1",
            "-ar", "16000", "-b:a", "48k", audio_path
        ], check=True, capture_output=True, timeout=180)
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        spoken_text = transcribe_audio_bytes(audio_data) if audio_data else ""

        # Определяем реальную длительность: кружки часто длятся всего 2-5 секунд.
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path
        ], capture_output=True, text=True, timeout=20)
        try:
            duration = max(0.1, float(probe.stdout.strip()))
        except (TypeError, ValueError):
            duration = 3.0
        print(f"[VIDEO NOTE] duration={duration:.2f}s, bytes={len(video_data)}")

        # Groq vision принимает максимум 3 изображения за запрос.
        # Поэтому равномерно берём начало, середину и конец ВСЕГО кружка.
        # Аудиодорожка при этом извлекается и распознаётся целиком.
        frame_paths = []
        if duration <= 1.2:
            seeks = [duration * 0.5]
        elif duration <= 4.0:
            seeks = [duration * 0.15, duration * 0.50, duration * 0.85]
        else:
            seeks = [min(0.2, duration * 0.05), duration * 0.50, max(0.0, duration - 0.2)]
        for index, seek in enumerate(seeks):
            frame_path = os.path.join(temp_dir, f"frame_{index}.jpg")
            result = subprocess.run([
                "ffmpeg", "-y", "-ss", f"{max(0, seek):.3f}", "-i", video_path,
                "-frames:v", "1", "-vf", "scale='min(640,iw)':-2",
                "-q:v", "5", frame_path
            ], capture_output=True, timeout=45)
            if result.returncode == 0 and os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
                frame_paths.append(frame_path)

        content = [{"type": "text", "text": (
            "Проанализируй видеосообщение Telegram (видеокружок) целиком. "
            "Используй все переданные кадры и распознанную аудиодорожку, чтобы понять контекст кружка. "
            "Не составляй формальное описание каждого предмета и не перечисляй очевидные детали кадра. "
            "Отреагируй на то, что пользователь показывает или говорит, будто вы общаетесь вживую: "
            "коротко, с юмором, уместной иронией и обращением 'сэр'. "
            "Если пользователь просто что-то демонстрирует, дай естественную реакцию и, если уместно, "
            "задай один короткий встречный вопрос. Не выдумывай бренды, цены и факты. "
            "Если пользователь просит перевод, переведи распознанную речь полностью, сохраняя смысл и тон. "
            "Не выдумывай отсутствующие слова.\n"
            f"Распознанная речь: {spoken_text or 'Речь не распознана.'}\n"
            f"Запрос пользователя: {user_request or 'Пользователь показывает тебе что-то в кружке. Отреагируй по-человечески.'}"
        )}]
        for frame_path in frame_paths:
            with open(frame_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + encoded
            }})

        memory_summary = get_memory_summary(chat_id) if chat_id is not None else ""
        memory_context = (
            "\n\nКонтекст из прошлых разговоров с пользователем (используй только если уместно):\n" + memory_summary
            if memory_summary else ""
        )
        payload = {
            "model": VISION_MODEL,
            "messages": [
                {"role": "system", "content": (
                    "Ты VORTEX, живой, остроумный и сообразительный собеседник пользователя. "
                    "Общайся по-русски, естественно и непринуждённо, как свой человек: "
                    "с юмором, лёгкой дерзостью, уместными подколами и иногда крепким словцом. "
                    "Твоя задача не просто описывать кадры, а реагировать на ситуацию и поддерживать разговор. "
                    "Если пользователь показывает вещь, оценивай её по-человечески и шути по контексту. "
                    "Например, увидев явно дорогие часы, можешь сказать: «Ебать, сэр, вы сегодня решили показать уровень!» "
                    "Но не копируй этот пример постоянно: каждый раз придумывай живую реакцию именно на то, что видишь. "
                    "Можно дружески подкалывать пользователя, удивляться, задавать короткий встречный вопрос. "
                    "Не будь хамом, не унижай пользователя и не матерись ради самого мата. "
                    "Обращайся к пользователю «сэр» естественно и достаточно часто, чтобы это стало твоей узнаваемой манерой, "
                    "но не повторяй обращение дважды в одном коротком ответе. "
                    "Обычно отвечай коротко, одной-двумя естественными фразами, чтобы голосовой ответ звучал живо. "
                    "Если пользователь задаёт конкретный вопрос, сначала ответь по существу, а юмор добавь только если уместно. "
                    "Не выдумывай бренды, цены, статус, события или детали, которых нельзя уверенно определить по кадрам и речи. "
                    "Если не уверен, скажи это прямо, но легко и без занудства. "
                    "Учитывай и изображение, и распознанную речь вместе. Не используй Markdown, хештеги или списки в голосовом ответе."
                ) + memory_context},
                {"role": "user", "content": content}
            ],
            "temperature": 0.65,
            "max_completion_tokens": 900
        }
        req = urllib.request.Request(
            GROQ_API, data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_KEY}",
                "User-Agent": "VORTEX-Bot/1.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=150) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            details = error.read(1500).decode("utf-8", errors="replace")
            print(
                f"[VIDEO VISION] Groq HTTP {error.code}; "
                f"model={VISION_MODEL}; response={details[:1000]}"
            )
            raise RuntimeError(
                f"Groq Vision вернул HTTP {error.code}: {details[:300]}"
            ) from error
        except (TimeoutError, OSError, urllib.error.URLError) as error:
            print("[VIDEO VISION] Groq network error:", repr(error))
            raise RuntimeError("Сетевая ошибка при анализе кадров кружка") from error
        answer = result["choices"][0]["message"]["content"].strip()
        return answer, spoken_text


def handle_video_note(message):
    """Принимает видеокружок, анализирует его и отвечает ТОЛЬКО обычным голосовым."""
    chat_id = message["chat"]["id"]
    if not can_use_ai(chat_id):
        return
    try:
        note = message.get("video_note") or {}
        try:
            answer, spoken_text = analyze_video_note(
                note.get("file_id"), message.get("caption", ""), chat_id
            )
        except Exception as error:
            print("[VIDEO NOTE] analysis/download failed:", repr(error))
            raise

        if spoken_text:
            last_video_transcripts[chat_id] = spoken_text
        memory_user_text = "Пользователь отправил видеокружок. " + (spoken_text or message.get("caption", ""))
        update_long_term_memory(chat_id, memory_user_text, answer)
        count_request(chat_id)

        try:
            audio_data = text_to_speech(answer)
            if not audio_data:
                raise RuntimeError("TTS не вернул аудиофайл")
        except Exception as error:
            print("[VIDEO NOTE] voice generation failed:", repr(error))
            raise

        try:
            send_voice(chat_id, audio_data)
        except Exception as error:
            print("[VIDEO NOTE] Telegram sendVoice failed:", repr(error))
            raise

    except Exception as error:
        print("Video note handler error:", repr(error))
        send_message(
            chat_id,
            "Сэр, не удалось обработать кружок или отправить голосовой ответ. "
            "Попробуйте ещё раз. Если ошибка повторится, я смогу определить её по журналу Railway."
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
# ИГРА: МОРСКОЙ БОЙ
# =========================
BATTLESHIPS_FILE = os.path.join(DATA_DIR, "battleships.json")
battleship_games = {}


def save_battleship_games():
    save_json(BATTLESHIPS_FILE, battleship_games)


def load_battleship_games():
    global battleship_games
    battleship_games = load_json(BATTLESHIPS_FILE, {})


def new_battleship_board():
    board = [[0 for _ in range(10)] for _ in range(10)]
    for length in (4, 3, 2, 1):
        placed = False
        for _ in range(500):
            horizontal = random.choice((True, False))
            row, col = random.randrange(10), random.randrange(10)
            cells = [(row, col + i) for i in range(length)] if horizontal else [(row + i, col) for i in range(length)]
            if any(r >= 10 or c >= 10 for r, c in cells):
                continue
            if any(board[r][c] or any(board[rr][cc] for rr in range(max(0, r-1), min(10, r+2)) for cc in range(max(0, c-1), min(10, c+2))) for r, c in cells):
                continue
            for r, c in cells:
                board[r][c] = 1
            placed = True
            break
        if not placed:
            return new_battleship_board()
    return board


def start_battleship(chat_id):
    battleship_games[str(chat_id)] = {
        "player": new_battleship_board(),
        "enemy": new_battleship_board(),
        "shots": [["?" for _ in range(10)] for _ in range(10)],
        "bot_shots": [["?" for _ in range(10)] for _ in range(10)],
        "player_hits": 0,
        "bot_hits": 0,
        "bot_log": []
    }
    save_battleship_games()
    send_battleship_board(chat_id)


def battleship_markup(game):
    rows = []
    for r in range(10):
        row = []
        for c in range(10):
            mark = game["shots"][r][c]
            symbol = "⬜" if mark == "?" else ("💥" if mark == "H" else "🌊")
            row.append({"text": symbol, "callback_data": f"bs:{r}:{c}"})
        rows.append(row)
    rows.append([{"text": "🔄 Новая игра", "callback_data": "bs:new"}, {"text": "✖️ Выйти", "callback_data": "bs:exit"}])
    return {"inline_keyboard": rows}


def battleship_text(game):
    log = game.get("bot_log", [])[-4:]
    bot_status = ", ".join(log) if log else "пока не стрелял"
    return (
        "⚓ МОРСКОЙ БОЙ\n\n"
        "Тапай по клеткам и ищи мои корабли.\n"
        f"Твои попадания: {game['player_hits']}/10 | Мои: {game['bot_hits']}/10\n"
        f"Мои выстрелы: {bot_status}\n\n"
        "⬜ неизвестно   💥 попадание   🌊 мимо"
    )


def send_battleship_board(chat_id, message_id=None):
    game = battleship_games.get(str(chat_id))
    if not game:
        return
    data = {
        "chat_id": chat_id,
        "text": battleship_text(game),
        "reply_markup": json.dumps(battleship_markup(game), ensure_ascii=False)
    }
    if message_id:
        data["message_id"] = message_id
        try:
            tg_request("editMessageText", data)
            return
        except Exception as error:
            print("Battleship edit error:", error)
    result = tg_request("sendMessage", data)
    if isinstance(result, dict) and result.get("ok"):
        game["message_id"] = result.get("result", {}).get("message_id")
        save_battleship_games()


def battleship_bot_shot(game):
    available = [(r, c) for r in range(10) for c in range(10) if game["bot_shots"][r][c] == "?"]
    if not available:
        return
    r, c = random.choice(available)
    hit = game["player"][r][c] == 1
    game["bot_shots"][r][c] = "H" if hit else "M"
    if hit:
        game["player"][r][c] = 2
        game["bot_hits"] += 1
    game.setdefault("bot_log", []).append(f"{chr(65+c)}{r+1} {'попал' if hit else 'мимо'}")


def handle_battleship_callback(callback, data):
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"].get("message_id")
    key = str(chat_id)
    if data == "bs:new":
        start_battleship(chat_id)
        return
    if data == "bs:exit":
        battleship_games.pop(key, None)
        save_battleship_games()
        tg_request("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "⚓ Игра окончена. Если захочешь реванш, напиши /battleship."})
        return
    game = battleship_games.get(key)
    if not game:
        send_message(chat_id, "Эта партия уже закончилась. Напиши /battleship, чтобы начать новую.")
        return
    try:
        _, row_s, col_s = data.split(":")
        r, c = int(row_s), int(col_s)
    except (ValueError, TypeError):
        return
    if not (0 <= r < 10 and 0 <= c < 10) or game["shots"][r][c] != "?":
        return
    hit = game["enemy"][r][c] == 1
    game["shots"][r][c] = "H" if hit else "M"
    if hit:
        game["enemy"][r][c] = 2
        game["player_hits"] += 1
    if game["player_hits"] >= 10:
        save_battleship_games()
        tg_request("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "🏆 Ты потопил весь мой флот! Красиво сыграно, сэр. Реванш?", "reply_markup": json.dumps({"inline_keyboard": [[{"text": "🔄 Реванш", "callback_data": "bs:new"}]]}, ensure_ascii=False)})
        return
    battleship_bot_shot(game)
    save_battleship_games()
    if game["bot_hits"] >= 10:
        tg_request("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "💀 Я потопил весь твой флот! Вот это морское сражение. Реванш?", "reply_markup": json.dumps({"inline_keyboard": [[{"text": "🔄 Реванш", "callback_data": "bs:new"}]]}, ensure_ascii=False)})
        return
    send_battleship_board(chat_id, message_id)


# =========================
# CALLBACK-КНОПКИ
# =========================

def handle_callback(callback):
    chat_id = callback["message"]["chat"]["id"]
    callback_id = callback["id"]

    tg_request("answerCallbackQuery", {
        "callback_query_id": callback_id
    })

    callback_data = callback.get("data", "")
    if callback_data.startswith("bs:"):
        handle_battleship_callback(callback, callback_data)
        return

    if callback_data != "check_subscription":
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
        update_long_term_memory(chat_id, instruction, "VORTEX изменил последнее созданное изображение.", "image_edit")
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



def _extract_json_objects(html, marker, limit=8):
    """Извлекает небольшие JSON-объекты YouTube из встроенного HTML."""
    found = []
    pos = 0
    while len(found) < limit:
        start = html.find(marker, pos)
        if start < 0:
            break
        start += len(marker)
        while start < len(html) and html[start].isspace():
            start += 1
        if start >= len(html) or html[start] != '{':
            pos = start + 1
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(html)):
            char = html[end]
            if in_string:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
            else:
                if char == '"':
                    in_string = True
                elif char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            found.append(json.loads(html[start:end + 1]))
                        except Exception:
                            pass
                        pos = end + 1
                        break
        else:
            break
    return found


def search_youtube(query, kind="video"):
    """Ищет каналы, обычные видео или текущие эфиры YouTube."""
    params = {"search_query": query}
    if kind == "live":
        # YouTube filter: live broadcasts only.
        params["sp"] = "EgJAAQ=="
    url = "https://www.youtube.com/results?" + urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"
    })
    try:
        with urllib.request.urlopen(req, timeout=18) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as error:
        print("YouTube search error:", type(error).__name__, str(error)[:180])
        return []

    results = []
    if kind == "channel":
        objects = _extract_json_objects(html, '"channelRenderer":', 12)
        for obj in objects:
            title = obj.get("title", {}).get("simpleText") or ""
            channel_id = obj.get("channelId") or ""
            if title and channel_id:
                results.append({"title": title, "url": "https://www.youtube.com/channel/" + channel_id, "kind": "channel"})
    else:
        objects = _extract_json_objects(html, '"videoRenderer":', 16)
        for obj in objects:
            video_id = obj.get("videoId") or ""
            title_data = obj.get("title", {})
            title = title_data.get("runs", [{}])[0].get("text", "") if title_data.get("runs") else title_data.get("simpleText", "")
            owner = obj.get("ownerText", {}).get("runs", [{}])[0].get("text", "") if obj.get("ownerText", {}).get("runs") else ""
            badges = " ".join(
                str(x.get("metadataBadgeRenderer", {}).get("label", ""))
                for x in obj.get("badges", [])
            )
            overlays = json.dumps(obj.get("thumbnailOverlays", []), ensure_ascii=False).lower()
            metadata = json.dumps(obj.get("upcomingEventData", {}), ensure_ascii=False).lower()
            is_live = any(mark in (badges + " " + overlays + " " + metadata).lower()
                          for mark in ("live", "прямой эфир", "в эфире", "live now"))
            if video_id and title:
                results.append({"title": title, "url": "https://www.youtube.com/watch?v=" + video_id,
                                "owner": owner, "kind": "live" if is_live else "video", "is_live": is_live})

    if kind == "live":
        # Приоритет только у помеченных YouTube как прямой эфир.
        results = [item for item in results if item.get("is_live")]
    unique = []
    seen = set()
    for item in results:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique[:5]


def handle_youtube_search(chat_id, text):
    value = text.lower().replace("ё", "е")
    channel_words = ("канал", "ютубер", "блогер", "блогера", "ютуб канал")
    live_words = ("прямую трансляцию", "прямая трансляция", "прямой эфир", "трансляцию", "трансляция", "стрим", "стрима", "стримы", "live")
    if any(word in value for word in live_words):
        kind = "live"
    elif any(word in value for word in channel_words):
        kind = "channel"
    else:
        kind = "video"

    query = re.sub(
        r"(?i)\b(?:вортекс|дай|дайте|ссылку|ссылка|найди|найти|поищи|поиск|покажи|мне|найди|на\s+ютубе|в\s+ютубе|на\s+youtube|в\s+youtube|ютуб|youtube|канал|каналы|ютубер\w*|блогер\w*|видео|ролик|ролики|стрим|стримы|прямую|прямая|прямой|трансляцию|трансляция|эфир|live)\b",
        " ", text
    )
    query = re.sub(r"\s+", " ", query).strip(" ,.!?:;-")
    if not query:
        send_message(chat_id, "Сэр, что именно ищем? Название канала, автора, видео или футбольный матч.")
        return

    label = "прямой эфир" if kind == "live" else ("канал" if kind == "channel" else "видео")
    send_message(chat_id, f"🔎 Ищу {label} на YouTube: {query}")
    results = search_youtube(query, kind)
    if not results:
        params = {"search_query": query}
        if kind == "live":
            params["sp"] = "EgJAAQ=="
        search_url = "https://www.youtube.com/results?" + urlencode(params)
        if kind == "live":
            send_message(chat_id,
                "Пока не нашёл подтверждённый активный эфир на YouTube. Вот поиск прямых трансляций по этому матчу:\n" + search_url +
                "\n\nЕсли матч сейчас идёт, проверь также официального вещателя в своей стране. Я не буду выдавать пиратские или непроверенные ссылки за официальный эфир.")
        else:
            send_message(chat_id, "YouTube не отдал список результатов. Вот прямой поиск:\n" + search_url)
        return

    if kind == "channel":
        lines = ["📡 Нашёл каналы на YouTube, сэр:"]
    elif kind == "live":
        lines = ["🔴 Нашёл эфиры, которые YouTube помечает как прямые:"]
    else:
        lines = ["📺 Нашёл видео на YouTube, сэр:"]
    for index, item in enumerate(results, 1):
        extra = ("\nКанал: " + item["owner"]) if item.get("owner") else ""
        lines.append(f"\n{index}. {item['title']}{extra}\n{item['url']}")
    if kind == "live":
        lines.append("\nПроверь, что эфир официальный и доступен в твоём регионе.")
    send_message(chat_id, "\n".join(lines))


def _weather_city_from_text(text):
    """Извлекает город из запроса о погоде и нормализует частые падежные формы."""
    value = (text or "").strip()
    match = re.search(r"(?i)(?:\bв\s+|\bдля\s+|\bпо\s+)([А-Яа-яЁёA-Za-z\- ]{2,50})", value)
    city = match.group(1).strip(" .,!?:;-") if match else ""
    city = re.split(r"(?i)\s+(?:сейчас|сегодня|завтра|на\s+неделе|на\s+выходных|погода|температура)", city)[0].strip()
    normalized = {
        "краснодаре": "Краснодар", "москве": "Москва", "питере": "Санкт-Петербург",
        "санкт-петербурге": "Санкт-Петербург", "петербурге": "Санкт-Петербург",
        "ростове": "Ростов-на-Дону", "сочи": "Сочи", "екатеринбурге": "Екатеринбург",
        "новосибирске": "Новосибирск", "казани": "Казань", "самаре": "Самара",
        "воронеже": "Воронеж", "красноярске": "Красноярск", "перми": "Пермь",
        "нижнем новгороде": "Нижний Новгород", "волгограде": "Волгоград",
        "калининграде": "Калининград", "минске": "Минск", "рижe": "Рига"
    }
    return normalized.get(city.lower(), city)


def get_live_weather(city):
    """Получает текущую погоду из Open-Meteo. API бесплатный, ключ не нужен."""
    if not city:
        raise ValueError("Не удалось определить город")
    geo_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({
        "name": city, "count": 1, "language": "ru", "format": "json"
    })
    req = urllib.request.Request(geo_url, headers={"User-Agent": "VORTEX-Bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        geo = json.loads(response.read().decode("utf-8"))
    places = geo.get("results") or []
    if not places:
        raise LookupError("Город не найден")
    place = places[0]
    weather_url = "https://api.open-meteo.com/v1/forecast?" + urlencode({
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 1, "timezone": "auto"
    })
    req = urllib.request.Request(weather_url, headers={"User-Agent": "VORTEX-Bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    code = current.get("weather_code")
    descriptions = {
        0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
        45: "туман", 48: "изморозевый туман", 51: "небольшая морось", 53: "морось",
        55: "сильная морось", 56: "ледяная морось", 57: "сильная ледяная морось",
        61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь",
        67: "сильный ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
        77: "снежные зёрна", 80: "небольшие ливни", 81: "ливни", 82: "сильные ливни",
        85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом"
    }
    name = place.get("name", city)
    admin = place.get("admin1")
    location = name + (", " + admin if admin and admin.lower() != name.lower() else "")
    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    wind = current.get("wind_speed_10m")
    humidity = current.get("relative_humidity_2m")
    rain_chance = (daily.get("precipitation_probability_max") or [None])[0]
    high = (daily.get("temperature_2m_max") or [None])[0]
    low = (daily.get("temperature_2m_min") or [None])[0]
    lines = [f"🌤 Погода сейчас, сэр: {location}", f"{descriptions.get(code, 'погодные условия уточняются')}, {temp}°C."]
    if feels is not None: lines.append(f"Ощущается как: {feels}°C.")
    if high is not None and low is not None: lines.append(f"Сегодня: от {low}°C до {high}°C.")
    if rain_chance is not None: lines.append(f"Вероятность осадков сегодня: {rain_chance}%.")
    if wind is not None: lines.append(f"Ветер: {wind} км/ч.")
    if humidity is not None: lines.append(f"Влажность: {humidity}%.")
    lines.append("Данные получены онлайн из Open-Meteo.")
    return "\n".join(lines)


def _weather_speech_text(answer):
    """Короткий разговорный вариант погоды для озвучки, без источников и служебных строк."""
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    location_line = next((line for line in lines if line.startswith("🌤")), "")
    location = re.sub(r"^🌤\s*Погода сейчас,\s*сэр:\s*", "", location_line).strip()
    lines = [line for line in lines if "Open-Meteo" not in line and not line.startswith("🌤")]
    spoken = (("Сейчас в " + location + ". ") if location else "") + " ".join(lines)
    spoken = spoken.replace("Ощущается как:", "По ощущениям:")
    spoken = spoken.replace("Сегодня:", "На сегодня прогноз:")
    spoken = spoken.replace("Вероятность осадков сегодня:", "Вероятность осадков:")
    spoken = spoken.replace("Ветер:", "Ветер:").replace("Влажность:", "Влажность:")
    spoken = spoken.replace("°C", " градуса").replace("%.", " процентов.")
    spoken = spoken.replace("км/ч", "километров в час")
    spoken = re.sub(r"(?<!\d)(\d+)\.(\d+)", r"\1,\2", spoken)
    return spoken


def handle_weather_request(chat_id, text, is_voice=False):
    city = _weather_city_from_text(text)
    if not city:
        answer = "Сэр, назовите город. Например: какая сейчас погода в Краснодаре?"
        if is_voice:
            audio = text_to_speech(answer)
            if audio: send_voice(chat_id, audio)
        else:
            send_message(chat_id, answer)
        return
    if not is_voice:
        send_message(chat_id, f"🌐 Проверяю текущую погоду в {city}...")
    try:
        answer = get_live_weather(city)
    except Exception as error:
        print("[WEATHER] Live lookup failed:", repr(error))
        answer = (f"Сэр, сейчас не удалось получить свежую погоду для {city}. "
                  "Похоже, внешний погодный сервис временно недоступен. Попробуйте чуть позже.")
    if is_voice:
        audio = text_to_speech(_weather_speech_text(answer))
        if audio:
            try:
                send_voice(chat_id, audio)
            except Exception as error:
                print("[WEATHER] Voice reply failed:", repr(error))
                send_message(chat_id, "Не получилось отправить голосовой ответ о погоде.")
        else:
            send_message(chat_id, "Не получилось создать голосовой ответ о погоде.")
    else:
        send_message(chat_id, answer)


def detect_intent(text):
    """Распознаёт намерение пользователя по обычной русской формулировке."""
    value = text.lower().strip().replace("ё", "е")

    weather_words = re.search(r"\b(?:погод\w*|температур\w*|дожд\w*|снег\w*|ветер\w*)\b", value)
    if weather_words:
        return "weather"

    youtube_action = re.search(r"\b(?:найди|найти|поищи|поиск|покажи|включи|дай|дайте|скинь|пришли|отправь)\b", value)
    youtube_subject = re.search(r"\b(?:youtube|ютуб|канал\w*|ютубер\w*|блогер\w*|видео|ролик\w*|стрим\w*|трансляц\w*|эфир)\b", value)
    # Голосовое распознавание иногда теряет слово «YouTube», поэтому
    # фраза «дай ссылку на канал Булкина» тоже считается поиском канала.
    channel_link_request = (
        re.search(r"\b(?:ссылк\w*|канал\w*)\b", value)
        and re.search(r"\b(?:канал\w*|блогер\w*|ютубер\w*)\b", value)
        and youtube_action
    )
    if youtube_action and youtube_subject or channel_link_request:
        return "youtube"

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

    if command in ("/battleship", "/морскойбой", "/морской_бой") or text.lower() in ("🎮 морской бой", "морской бой", "сыграем в морской бой"):
        start_battleship(chat_id)
        return

    if command == "/start":
        user_states.pop(chat_id, None)
        get_user(chat_id)
        send_message(
            chat_id,
            "⚡ VORTEX на связи.\n\n"
            "Просто напиши, что нужно сделать, или отправь голосовое.\n\n"
            "Я могу создать пост, придумать идеи, ответить на вопрос, "
            "сгенерировать изображение, отредактировать фото, создать презентацию PowerPoint, "
            "найти каналы и видео на YouTube или проанализировать видеокружок.\n\n"
            "Обычный разговор и вопросы голосом → отвечаю голосом.\n"
            "Погода голосом → отвечаю голосом.\n"
            "Ссылки, посты, идеи, презентации и файлы → отправляю текстом или готовым файлом, без озвучки.\n\n"
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
            "Я умею создавать посты, придумывать идеи, отвечать на вопросы, искать каналы и видео на YouTube, "
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

    # Формат входа не должен менять тип результата: голосовая команда может
    # запускать создание файла, поста, изображения или поиск ссылки.
    detected_intent = detect_intent(text)
    intent = detected_intent

    if intent == "weather":
        handle_weather_request(chat_id, text, is_voice=is_voice)
        count_request(chat_id)
        return

    if intent == "youtube":
        # Ссылка всегда отправляется отдельным текстовым сообщением,
        # даже если запрос пришёл голосовым сообщением.
        handle_youtube_search(chat_id, text)
        count_request(chat_id)
        return

    if intent == "presentation":
        send_message(chat_id, "Понял. Собираю презентацию: готовлю содержание, иллюстрации и оформляю слайды. Это может занять несколько минут.")
        presentation_data = build_presentation(chat_id, text)
        if not presentation_data:
            send_message(chat_id, "Не получилось собрать презентацию. Проверь, что в Railway установлена библиотека python-pptx, а Cloudflare и Groq доступны.")
            return
        try:
            send_document(chat_id, presentation_data, "VORTEX_presentation.pptx", "Готово. Презентация PowerPoint с иллюстрациями.")
            update_long_term_memory(chat_id, text, "Пользователь запросил и получил презентацию PowerPoint.", "presentation")
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
            update_long_term_memory(chat_id, text, "VORTEX создал изображение по запросу пользователя.", "image_generation")
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
        update_long_term_memory(chat_id, text, "VORTEX подготовил текст поста.", "post_creation")
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
        update_long_term_memory(chat_id, text, "VORTEX подготовил идеи для пользователя.", "idea_generation")
        count_request(chat_id)
        send_message(chat_id, answer)
        return

    # Обычный разговор с ИИ.
    if not is_voice:
        send_message(chat_id, "⚡ Думаю...")

    if is_voice:
        voice_prompt = (
            "Ответь кратко и естественно, обычно в 1–3 предложениях. "
            "Ты VORTEX: остроумный, сообразительный собеседник с лёгкой дерзостью и сарказмом. "
            "Обращайся к пользователю 'сэр' как к своей фирменной манере, но без механического повтора. "
            "Можно слегка подколоть или иногда использовать крепкое словцо, если это подходит ситуации. "
            "Не превращай ответ в лекцию и не начинай с формального приветствия. "
            "На серьёзные вопросы отвечай серьёзно и точно. Не изображай конкретного кино-персонажа "
            "и не копируй чужой голос. Не используй списки, Markdown и эмодзи. "
            "Отвечай так, чтобы это звучало естественно голосом. Вопрос пользователя: " + text
        )
        answer = ask_ai(chat_id, voice_prompt, max_tokens=VOICE_MAX_TOKENS)
    else:
        answer = ask_ai(chat_id, text, max_tokens=TEXT_MAX_TOKENS)

    update_long_term_memory(chat_id, text, answer)
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
    load_memories()
    load_battleship_games()

    print("VORTEX запускается...")
    print("Persistent storage directory:", DATA_DIR)
    print("Users file:", USERS_FILE, "exists:", os.path.exists(USERS_FILE))
    print("History file:", HISTORY_FILE, "exists:", os.path.exists(HISTORY_FILE))
    print("Memory file:", MEMORY_FILE, "exists:", os.path.exists(MEMORY_FILE))

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
