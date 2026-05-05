import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION = os.getenv("GCP_LOCATION") or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GCP_SERVICE_ACCOUNT_PATH = (
    os.getenv("GCP_SERVICE_ACCOUNT_PATH")
    or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    or ""
)

_missing = [k for k, v in {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "ELEVENLABS_API_KEY": ELEVENLABS_API_KEY,
}.items() if not v]

if _missing:
    raise RuntimeError(
        f"Missing required environment variables: {', '.join(_missing)}. "
        "Copy .env.example to .env and fill in your API keys."
    )

_sessions_dir_env = os.getenv("SESSIONS_DIR")
SESSIONS_DIR = Path(_sessions_dir_env).expanduser() if _sessions_dir_env else BASE_DIR / "sessions"
if not SESSIONS_DIR.is_absolute():
    SESSIONS_DIR = BASE_DIR / SESSIONS_DIR
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

MIN_DISK_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

OPENAI_MODEL = "gpt-4.1"
GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
GEMINI_IMAGE_MODEL = "gemini-3-pro-image-preview"
VEO_STANDARD_MODEL = os.getenv("VEO_STANDARD_MODEL", "veo-3.1-generate-001")
VEO_FAST_MODEL = os.getenv("VEO_FAST_MODEL", "veo-3.1-fast-generate-001")
VEO_RESOLUTION = os.getenv("VEO_RESOLUTION", "720p")
VEO_SAMPLE_COUNT = max(1, min(4, int(os.getenv("VEO_SAMPLE_COUNT", "2"))))


def _float_env(name: str, default: float, min_value: float, max_value: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def refresh_elevenlabs_env() -> None:
    """Reload ElevenLabs knobs so .env voice changes apply without app restart."""
    load_dotenv(BASE_DIR / ".env", override=True)


def get_elevenlabs_api_key() -> str:
    refresh_elevenlabs_env()
    return os.getenv("ELEVENLABS_API_KEY", "")


def get_elevenlabs_tts_model() -> str:
    refresh_elevenlabs_env()
    return os.getenv("ELEVENLABS_TTS_MODEL", "eleven_v3")


def get_elevenlabs_tts_output_format() -> str:
    refresh_elevenlabs_env()
    return os.getenv("ELEVENLABS_TTS_OUTPUT_FORMAT", "mp3_44100_128")


def get_elevenlabs_tts_language_code() -> str:
    refresh_elevenlabs_env()
    return os.getenv("ELEVENLABS_TTS_LANGUAGE_CODE", "en").strip()


def get_elevenlabs_tts_voice_settings() -> dict:
    refresh_elevenlabs_env()
    return {
        "stability": _float_env("ELEVENLABS_TTS_STABILITY", 0.45, 0.0, 1.0),
        "similarity_boost": _float_env("ELEVENLABS_TTS_SIMILARITY_BOOST", 0.9, 0.0, 1.0),
        "style": _float_env("ELEVENLABS_TTS_STYLE", 0.25, 0.0, 1.0),
        "speed": _float_env("ELEVENLABS_TTS_SPEED", 1.2, 0.7, 1.2),
        "use_speaker_boost": _bool_env("ELEVENLABS_TTS_USE_SPEAKER_BOOST", True),
    }


def get_elevenlabs_voice_ids() -> dict[str, str]:
    refresh_elevenlabs_env()
    return {
        "male": os.getenv("ELEVENLABS_MALE_VOICE_ID", "PdJQAOWyIMAQwD7gQcSc"),
        "female": os.getenv("ELEVENLABS_FEMALE_VOICE_ID", "Cvv0EXhC1Zv7b4a2QfWl"),
    }


def get_elevenlabs_audio_tempo() -> float:
    refresh_elevenlabs_env()
    return _float_env("ELEVENLABS_AUDIO_TEMPO", 1.35, 1.0, 2.0)


ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_v3")
ELEVENLABS_TTS_OUTPUT_FORMAT = os.getenv(
    "ELEVENLABS_TTS_OUTPUT_FORMAT",
    "mp3_44100_128",
)
ELEVENLABS_TTS_LANGUAGE_CODE = get_elevenlabs_tts_language_code()
ELEVENLABS_TTS_VOICE_SETTINGS = get_elevenlabs_tts_voice_settings()
ELEVENLABS_VOICE_IDS = get_elevenlabs_voice_ids()
ELEVENLABS_AUDIO_TEMPO = get_elevenlabs_audio_tempo()

VEO_POLL_INTERVAL_SEC = 10
VEO_MAX_POLLS = 90  # 15 minutes hard cap

HOOK_CATEGORIES = [
    "Storytelling",
    "Authority",
    "Myth_busting",
    "Comparison",
    "Educational",
    "Day_in_the_Life",
    "Pattern_Interrupt",
]
