import os
from functools import lru_cache
from pathlib import Path

from .secrets import get_key

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("NUDGEY_DATA_DIR", ROOT / "backend" / "data"))
DB_PATH = DATA_DIR / "nudgeycart.sqlite3"

# Render sets RENDER_EXTERNAL_URL on every web service, so the hosted demo needs no extra setting.
PUBLIC_URL = (os.environ.get("NUDGEY_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
              or "http://localhost:8797").rstrip("/")
# Haiku: the decision sits between "shopper moves to leave" and the dialog, so latency wins.
MODEL = os.environ.get("NUDGEY_MODEL", "claude-haiku-5-5")
EFFORT = os.environ.get("NUDGEY_EFFORT", "low")
LLM_TIMEOUT_S = float(os.environ.get("NUDGEY_LLM_TIMEOUT", "20"))

PAYPAL_SECRETS = "paypal-sandbox.env"
ANTHROPIC_SECRETS = "claude-servikaki.env"
CHANNEL3_SECRETS = "channel3.env"

# Demo stores (WordPress Playground) self-register; this caps sign-ups per IP and hour.
DEMO_SIGNUPS_PER_HOUR = int(os.environ.get("NUDGEY_DEMO_SIGNUPS_PER_HOUR", "30"))


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


@lru_cache(maxsize=1)
def channel3_api_key() -> str:
    """Optional: without a key the price match lever is simply never offered.
    Cached, including a miss, so a missing key does not cost an SSM call per request."""
    try:
        return get_key(CHANNEL3_SECRETS, "CHANNEL3_API_KEY")
    except Exception:  # missing parameter, no AWS access, no env var
        return ""
