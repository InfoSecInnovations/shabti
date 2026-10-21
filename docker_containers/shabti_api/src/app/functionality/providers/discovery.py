"""Which models each provider offers, and what they are for.

llama.cpp's router tags its models, so `local` is described by the installer's catalogue. A remote
provider only lists ids, so the rest comes from the admin's overrides, then anything the listing
volunteers, then the bundled models.dev snapshot, and finally conservative defaults. How a model can
be told to think is found the same way, except that a local model's comes from its chat template.
"""

import asyncio
import hashlib
import logging
import time
import httpx
from shabti_types import ProviderModelInfo, ProviderUnavailableError, ShabtiError
from ..embeddings_config import optional_settings
from ..models import get_models, model_categories, model_status
from .config import LOCAL, Provider, config_mtime
from .errors import provider_error
from .keys import shared_key, user_key
from .models_dev import lookup
from .reasoning import (
    NO_CAPABILITY,
    Capability,
    first_known,
    from_listing,
    from_models_dev,
    from_override,
    from_template,
)
from .registry import local_url, providers, qualify

# a remote listing barely changes, and listing every provider on each prompt would cost each one a
# round trip. failures are not kept, so a provider that comes back is listed on the next request.
# a listing made with a user's own key is kept apart from the others, as their account may be
# offered different models
CACHE_SECONDS = 600
REQUEST_TIMEOUT = 10
# small enough that a chunk sized for it fits any chat model still in use
FALLBACK_CONTEXT_LENGTH = 8192

_cache: dict[str, tuple[float, list[ProviderModelInfo]]] = {}
_cached_config: float | None = None
# what each loaded local model's template reads. llama.cpp can only show the template of a model it
# has loaded, and one loaded again may have been swapped for another under the same name
_local_reasoning: dict[str, Capability] = {}


def clear_cache():
    _cache.clear()
    _local_reasoning.clear()


def request_headers(provider: Provider, key: str | None) -> dict[str, str]:
    """The configured headers and the key, and nothing else the API was sent."""
    headers = dict(provider.headers)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def fetch_models(provider: Provider, key: str | None) -> list[dict]:
    try:
        # never redirected: the key would follow the request wherever it was sent
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.get(
                f"{provider.base_url}/models", headers=request_headers(provider, key)
            )
    except httpx.HTTPError:
        raise ProviderUnavailableError(provider.id) from None
    # most often a base URL missing its version, rather than a model that doesn't exist
    if response.status_code == 404:
        raise ProviderUnavailableError(
            provider.id, message=f"{provider.name} has no model list at its base URL"
        )
    if response.status_code != 200:
        raise provider_error(response.status_code, provider.id)
    try:
        listed = response.json()["data"]
        return [x for x in listed if isinstance(x.get("id"), str)]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ProviderUnavailableError(
            provider.id, message=f"{provider.name} did not list its models"
        ) from None


def category(model_id: str, override: dict) -> str:
    if override.get("category"):
        return override["category"]
    # nothing reliable says what a listed model is for, but embeddings models are named for it
    return "embeddings" if "embed" in model_id.lower() else "chat"


def describe(provider: Provider, listed: dict) -> ProviderModelInfo:
    model_id = listed["id"]
    override = provider.overrides.get(model_id) or {}
    known = lookup(provider.base_url, model_id) or {}
    context_length = (
        override.get("context_length")
        or listed.get("context_length")
        or (known.get("limit") or {}).get("context")
        or FALLBACK_CONTEXT_LENGTH
    )
    reasoning, reasoning_default = from_override(override) or first_known(
        from_listing(listed), from_models_dev(known)
    )
    return ProviderModelInfo(
        id=qualify(provider.id, model_id),
        provider_id=provider.id,
        model_id=model_id,
        display_name=override.get("label") or model_id,
        category=category(model_id, override),
        context_length=int(context_length),
        reasoning=reasoning,
        reasoning_default=reasoning_default,
    )


async def remote_models(
    provider: Provider, credentials: str | None
) -> list[ProviderModelInfo]:
    global _cached_config
    # a provider edited since its models were listed may now point somewhere else entirely
    if (modified := config_mtime()) != _cached_config:
        clear_cache()
        _cached_config = modified
    # a provider that only embeds offers nothing to chat with, so there's nothing to ask it
    if provider.allow == ():
        return []
    own = await user_key(provider, credentials)
    # nothing the user could prompt it with until they add their own key
    if provider.credential_mode == "user" and not own:
        return []
    # the admin's key where there is one, so most users share one listing
    shared = shared_key(provider)
    key = shared or own
    scope = (
        provider.id
        if shared or not own
        else f"{provider.id}#{hashlib.sha256(own.encode()).hexdigest()}"
    )
    cached = _cache.get(scope)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    listed = await fetch_models(provider, key)
    if provider.allow is not None:
        listed = [x for x in listed if x["id"] in provider.allow]
    models = [describe(provider, x) for x in listed]
    _cache[scope] = (time.monotonic(), models)
    return models


async def chat_template(model_id: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # without autoload, so asking can never load a model in place of another
            response = await client.get(
                local_url("/props"), params={"model": model_id, "autoload": 0}
            )
        return response.json().get("chat_template") if response.is_success else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def catalogue_override(model_id: str) -> dict:
    try:
        return optional_settings(qualify(LOCAL, model_id))
    except ShabtiError:
        # a model listing is no place to report a broken installation, embedding does that
        return {}


async def local_reasoning(model: dict) -> Capability:
    if override := from_override(catalogue_override(model["id"])):
        return override
    if model_status(model) != "loaded":
        return NO_CAPABILITY
    if model["id"] not in _local_reasoning:
        template = await chat_template(model["id"])
        # not kept when it couldn't be read, so it is asked for again
        if template is None:
            return NO_CAPABILITY
        _local_reasoning[model["id"]] = from_template(template)
    return _local_reasoning[model["id"]]


async def local_models() -> list[ProviderModelInfo]:
    # not cached, as the status changes as models are loaded
    listed = (await get_models())["data"]
    loaded = {x["id"] for x in listed if model_status(x) == "loaded"}
    for model_id in [x for x in _local_reasoning if x not in loaded]:
        del _local_reasoning[model_id]
    models = []
    for x in listed:
        categories = model_categories(x)
        # only a chat model is asked for its template, as nothing else is prompted
        chat = await local_reasoning(x) if "chat" in categories else NO_CAPABILITY
        models += [
            ProviderModelInfo(
                id=qualify(LOCAL, x["id"]),
                provider_id=LOCAL,
                model_id=x["id"],
                display_name=x["id"],
                category=model_category,
                status=model_status(x),
                reasoning=chat[0] if model_category == "chat" else None,
                reasoning_default=chat[1] if model_category == "chat" else None,
            )
            for model_category in sorted(categories)
        ]
    return models


async def list_models(
    provider: Provider, credentials: str | None, model_category: str | None = None
) -> list[ProviderModelInfo]:
    models = await (
        local_models() if provider.id == LOCAL else remote_models(provider, credentials)
    )
    return [x for x in models if not model_category or x.category == model_category]


async def list_all_models(
    model_category: str | None, credentials: str | None
) -> list[ProviderModelInfo]:
    """Every provider's models. One that can't be reached is left out rather than failing the rest."""
    found = []
    every = providers()
    results = await asyncio.gather(
        *(list_models(x, credentials, model_category) for x in every),
        return_exceptions=True,
    )
    for provider, result in zip(every, results):
        if isinstance(result, ShabtiError):
            logging.getLogger("shabti").warning(
                "could not list the models of %s: %s", provider.id, result.message
            )
        elif isinstance(result, Exception):
            # by type alone: the text of an unexpected error could carry a header
            logging.getLogger("shabti").warning(
                "could not list the models of %s: %s",
                provider.id,
                type(result).__name__,
            )
        elif isinstance(result, BaseException):
            raise result
        else:
            found += result
    return found
