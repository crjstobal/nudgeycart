import os
from pathlib import Path

from .secrets import get_key

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("NUDGEY_DATA_DIR", ROOT / "backend" / "data"))
DB_PATH = DATA_DIR / "nudgeycart.sqlite3"

PUBLIC_URL = os.environ.get("NUDGEY_PUBLIC_URL", "http://localhost:8797").rstrip("/")
MODEL = os.environ.get("NUDGEY_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("NUDGEY_EFFORT", "low")
LLM_TIMEOUT_S = float(os.environ.get("NUDGEY_LLM_TIMEOUT", "20"))

PAYPAL_SECRETS = "paypal-sandbox.env"
ANTHROPIC_SECRETS = "claude-servikaki.env"


def paypal_base() -> str:
    return get_key(PAYPAL_SECRETS, "PAYPAL_API_BASE").rstrip("/")


def paypal_client_id() -> str:
    return get_key(PAYPAL_SECRETS, "PAYPAL_CLIENT_ID")


def paypal_client_secret() -> str:
    return get_key(PAYPAL_SECRETS, "PAYPAL_CLIENT_SECRET")


def paypal_env() -> str:
    return get_key(PAYPAL_SECRETS, "PAYPAL_ENV")


def anthropic_api_key() -> str:
    return get_key(ANTHROPIC_SECRETS, "ANTHROPIC_API_KEY")
