"""What models.dev knows about a model, from the copy the configurator fetches on each launch.

`GET /models` only promises an id, so this is where a remote model's context length comes from.
Without a copy, discovery falls back on what the listing says and then on its defaults.
"""

import json
import logging
import os
from urllib.parse import urlparse
from .files import read_cached

SNAPSHOT_PATH = os.getenv("SHABTI_MODELS_DEV", "/opt/shabti_config/models-dev.json")

# providers with an SDK of their own, which models.dev lists without the URL they are served on
HOST_ALIASES = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "google",
}


def snapshot() -> dict:
    try:
        return read_cached(SNAPSHOT_PATH, json.loads) or {}
    except (OSError, ValueError):
        # discovery falls back on its defaults, which is no reason to refuse to list models
        logging.getLogger("shabti").warning("could not read %s", SNAPSHOT_PATH)
        return {}


def host(url: str | None) -> str | None:
    return urlparse(url).hostname if url else None


def lookup(base_url: str, model_id: str) -> dict | None:
    """The entry for this model, preferring the provider served from the same host.

    The same id can mean different limits from different hosts, but an aggregator or a self-hosted
    server is still most likely serving the model its id names, so any match beats none.
    """
    providers = snapshot().get("providers", {})
    base_host = host(base_url)
    for provider_id, provider in providers.items():
        if base_host and (
            HOST_ALIASES.get(base_host) == provider_id
            or host(provider.get("api")) == base_host
        ):
            if model := provider.get("models", {}).get(model_id):
                return model
    return next(
        (
            model
            for provider in providers.values()
            if (model := provider.get("models", {}).get(model_id))
        ),
        None,
    )
