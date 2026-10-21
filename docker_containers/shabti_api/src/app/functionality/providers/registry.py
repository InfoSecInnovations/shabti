"""Every provider a model can come from, and the ids that name those models.

A model is named `<provider_id>/<model_id>` everywhere outside a provider. Only the first `/`
separates them, since remote model ids often contain one of their own.
"""

import os
from dataclasses import dataclass
from shabti_types import ModelNotFoundError, ProviderInfo
from .config import LOCAL, Provider, providers_config
from .keys import has_shared_key


def local_url(path: str) -> str:
    return f"http://{os.getenv('LLM_HOST')}:11434{path}"


def local_provider() -> Provider:
    return Provider(id=LOCAL, name="Local models", base_url=local_url("/v1"))


def providers() -> list[Provider]:
    # llama.cpp is only part of an installation that has local models
    local = [local_provider()] if os.getenv("LLM_HOST") else []
    return local + list(providers_config().providers)


def get_provider(provider_id: str) -> Provider:
    provider = next((x for x in providers() if x.id == provider_id), None)
    if not provider:
        raise ModelNotFoundError(message=f"There is no model provider {provider_id}")
    return provider


def provider_info(provider: Provider) -> ProviderInfo:
    local = provider.id == LOCAL
    return ProviderInfo(
        id=provider.id,
        name=provider.name,
        credential_mode="none" if local else provider.credential_mode,
        has_shared_key=not local and has_shared_key(provider),
    )


def qualify(provider_id: str, model_id: str) -> str:
    return f"{provider_id}/{model_id}"


def split_model_id(qualified: str) -> tuple[str, str]:
    provider_id, _, model_id = qualified.partition("/")
    if not provider_id or not model_id:
        raise ModelNotFoundError(
            model=qualified,
            message=f"{qualified} doesn't name its provider, as <provider>/<model>",
        )
    return provider_id, model_id


@dataclass(frozen=True)
class ChatModel:
    """The model a prompt answers with, and the provider that serves it."""

    provider: Provider
    model_id: str
    # the values a prompt may ask it to think at, see `reasoning`
    reasoning: tuple[bool | str, ...] = ()

    @property
    def id(self) -> str:
        return qualify(self.provider.id, self.model_id)
