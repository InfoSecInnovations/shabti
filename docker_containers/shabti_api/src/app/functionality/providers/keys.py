from .config import LOCAL, Provider
from .secret_files import provider_keys


# credentials identify the requester, who will be able to bring their own key to some providers
def effective_key(provider: Provider, credentials: str | None) -> str | None:
    """The key a request to this provider is made with, or None for one that takes no key."""
    if provider.id == LOCAL:
        return None
    return provider_keys().get(provider.id) or None


def has_shared_key(provider: Provider) -> bool:
    return bool(provider_keys().get(provider.id))
