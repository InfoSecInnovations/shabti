"""Secrets the configurator writes into a directory mounted only into the API.

Nothing read from these files goes into a message or a log: a parse error could quote it.
"""

import base64
import json
import os
import re
from shabti_types import ProviderConfigError
from .files import read_cached

SECRETS_DIR = os.getenv("SHABTI_SECRETS_DIR", "/run/secrets")

PROVIDER_KEYS = "shabti_provider_keys.json"
INSTANCE_SALT = "shabti_instance_salt"
SECRETS_KEY = "shabti_secrets_key.json"

# AES-256
KEK_BYTES = 32
VERSION = re.compile(r"[0-9]+")


def provider_keys() -> dict[str, str]:
    """The admin's key for each provider, by provider id."""
    try:
        keys = read_cached(os.path.join(SECRETS_DIR, PROVIDER_KEYS), json.loads)
    except (OSError, ValueError):
        raise ProviderConfigError(
            message=f"the provider keys in {SECRETS_DIR} could not be read"
        ) from None
    if keys is not None and not isinstance(keys, dict):
        raise ProviderConfigError(
            message=f"the provider keys in {SECRETS_DIR} must be an object"
        )
    return keys or {}


def instance_salt() -> str | None:
    try:
        salt = read_cached(os.path.join(SECRETS_DIR, INSTANCE_SALT), str.strip)
    except OSError:
        return None
    return salt or None


def parse_secrets_key(text: str) -> tuple[str, dict[str, bytes]]:
    raw = json.loads(text)
    current = str(raw["current"])
    keys = {str(k): base64.b64decode(v, validate=True) for k, v in raw["keys"].items()}
    if (
        current not in keys
        # a version goes into each record between colons
        or not all(VERSION.fullmatch(x) for x in keys)
        or any(len(x) != KEK_BYTES for x in keys.values())
    ):
        raise ValueError("unusable secrets key")
    return current, keys


def secrets_key() -> tuple[str, dict[str, bytes]]:
    """The keys users' provider keys are encrypted with: the version new ones use, and every
    version still able to decrypt, by version.

    Rotating adds a version rather than replacing one, so keys sealed under an older version can
    still be read until they are re-encrypted.
    """
    try:
        parsed = read_cached(os.path.join(SECRETS_DIR, SECRETS_KEY), parse_secrets_key)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ProviderConfigError(
            message=f"the encryption key in {SECRETS_DIR} could not be read"
        ) from None
    if parsed is None:
        raise ProviderConfigError(
            message=f"there is no encryption key in {SECRETS_DIR}"
        )
    return parsed
