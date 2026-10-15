"""Secrets are read at runtime from AWS SSM Parameter Store, never from disk.

Each parameter holds a whole .env file (KEY=value lines). In environments without
AWS access (e.g. Render), the same keys can be provided as plain environment variables.
"""
import os
from functools import lru_cache

SSM_PREFIX = os.environ.get("NUDGEY_SSM_PREFIX", "/servikaki/secrets/")
SSM_PROFILE = os.environ.get("NUDGEY_SSM_PROFILE", "servikaki-secrets")
SSM_REGION = os.environ.get("NUDGEY_SSM_REGION", "eu-west-1")


@lru_cache(maxsize=None)
def _load(env_file: str) -> dict:
    import boto3

    session = boto3.Session(profile_name=SSM_PROFILE, region_name=SSM_REGION)
    raw = session.client("ssm").get_parameter(
        Name=f"{SSM_PREFIX}{env_file}", WithDecryption=True
    )["Parameter"]["Value"]
    out = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get_key(env_file: str, key: str) -> str:
    """Environment variable wins; otherwise read the key from SSM."""
    if os.environ.get(key):
        return os.environ[key]
    return _load(env_file)[key]
