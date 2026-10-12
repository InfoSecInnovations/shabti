"""Secrets the configurator writes into a directory mounted only into the API.

Nothing read from these files goes into a message or a log: a parse error could quote it.
"""

import json
import os
from shabti_types import ProviderConfigError
from .files import read_cached

SECRETS_DIR = os.getenv("SHABTI_SECRETS_DIR", "/run/secrets")

PROVIDER_KEYS = "shabti_provider_keys.json"
INSTANCE_SALT = "shabti_instance_salt"


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
