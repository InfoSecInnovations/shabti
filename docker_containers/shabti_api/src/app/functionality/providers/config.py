"""The model providers the admin added, as the configurator recorded them.

llama.cpp isn't in here: it is the built-in `local` provider, present whenever it is installed, see
`registry`. Without the file there are no other providers, which is an ordinary installation.
"""

import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse
from shabti_types import ProviderConfigError
from .files import mtime, read_cached

CONFIG_PATH = os.getenv(
    "SHABTI_PROVIDERS_CONFIG", "/opt/shabti_config/shabti-providers.json"
)

# the id of llama.cpp, which no configured provider may take
LOCAL = "local"

SLUG = re.compile(r"[a-z0-9-]+")

CREDENTIAL_MODES = ("shared",)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    # up to and including the version, e.g. https://api.example.com/v1, with no trailing slash
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)
    credential_mode: str = "shared"
    # the request field the hashed requester goes in, for providers that name it differently
    user_id_field: str = "user"
    # the models offered, by the provider's own id. None offers every model it lists, and empty
    # offers none, for a provider that only embeds
    allow: tuple[str, ...] | None = None
    # what the admin knows better than discovery, by the provider's own model id
    overrides: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingsConfig:
    # qualified, like the default chat model
    model: str
    # asked of a model that can shorten its vectors, otherwise it makes its natural size
    dimensions: int | None = None


@dataclass(frozen=True)
class ProvidersConfig:
    providers: tuple[Provider, ...] = ()
    # qualified, so it can name a local model as well as a remote one
    default_chat_model: str | None = None
    # None embeds with whichever local model carries the embeddings tag
    embeddings: EmbeddingsConfig | None = None


def invalid(message: str) -> ProviderConfigError:
    return ProviderConfigError(message=f"{CONFIG_PATH}: {message}")


def parse_provider(raw) -> Provider:
    if not isinstance(raw, dict):
        raise invalid("each provider must be an object")
    provider_id = raw.get("id")
    if not isinstance(provider_id, str) or not SLUG.fullmatch(provider_id):
        raise invalid(f"{provider_id!r} is not a valid provider id")
    if provider_id == LOCAL:
        raise invalid(f'"{LOCAL}" is reserved for the bundled llama.cpp')
    base_url = raw.get("base_url")
    parsed = urlparse(base_url) if isinstance(base_url, str) else None
    if not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise invalid(f"{provider_id} has no http(s) base_url")
    credential_mode = raw.get("credential_mode", "shared")
    if credential_mode not in CREDENTIAL_MODES:
        raise invalid(f"{provider_id} has an unknown credential_mode")
    models = raw.get("models") or {}
    return Provider(
        id=provider_id,
        name=raw.get("name") or provider_id,
        base_url=base_url.rstrip("/"),
        headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
        credential_mode=credential_mode,
        user_id_field=raw.get("user_id_field") or "user",
        allow=None if models.get("allow") is None else tuple(models["allow"]),
        overrides=dict(models.get("overrides") or {}),
    )


def parse_embeddings(raw, providers: tuple[Provider, ...]) -> EmbeddingsConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise invalid("embeddings must be an object")
    model = raw.get("model")
    provider_id, _, model_id = (model if isinstance(model, str) else "").partition("/")
    if not provider_id or not model_id:
        raise invalid("the embeddings model must be named as <provider>/<model>")
    if provider_id != LOCAL and provider_id not in {x.id for x in providers}:
        raise invalid(f"the embeddings model's provider {provider_id} isn't configured")
    dimensions = raw.get("dimensions")
    # a bool is an int as far as isinstance is concerned
    whole = isinstance(dimensions, int) and not isinstance(dimensions, bool)
    if dimensions is not None and not (whole and dimensions > 0):
        raise invalid("the embeddings dimensions must be a positive whole number")
    return EmbeddingsConfig(model=model, dimensions=dimensions)


def parse_config(text: str) -> ProvidersConfig:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise invalid("the file must hold an object")
    providers = tuple(parse_provider(x) for x in raw.get("providers") or ())
    ids = [x.id for x in providers]
    if len(ids) != len(set(ids)):
        raise invalid("provider ids must be unique")
    return ProvidersConfig(
        providers=providers,
        default_chat_model=raw.get("default_chat_model"),
        embeddings=parse_embeddings(raw.get("embeddings"), providers),
    )


def providers_config() -> ProvidersConfig:
    try:
        config = read_cached(CONFIG_PATH, parse_config)
    except (OSError, ValueError) as e:
        raise invalid(f"could not be read: {e}") from e
    return config or ProvidersConfig()


def config_mtime() -> float | None:
    """Changes whenever the providers do, so what was learned about them can be thrown away."""
    return mtime(CONFIG_PATH)
