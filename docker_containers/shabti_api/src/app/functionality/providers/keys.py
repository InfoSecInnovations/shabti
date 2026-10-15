import os
from shabti_types import ProviderKeyMissingError
from shabti_util import auth_enabled
from .config import LOCAL, Provider
from .credentials import get_user_key
from .secret_files import provider_keys

# the credential modes in which users can add keys of their own
USER_KEY_MODES = ("user", "shared_or_user")


def user_keys_enabled() -> bool:
    """Only with security fully enabled: a disabled install has no users to hold a key, and the
    demo's accounts are shared by everyone who has the password."""
    return auth_enabled() and os.getenv("IS_SECURITY_DEMO") != "True"


def accepts_user_keys(provider: Provider) -> bool:
    return provider.credential_mode in USER_KEY_MODES and user_keys_enabled()


def shared_key(provider: Provider) -> str | None:
    """The admin's key for the provider, which is all embeddings and status checks ever use."""
    if provider.id == LOCAL:
        return None
    return provider_keys().get(provider.id) or None


async def user_key(provider: Provider, credentials: str | None) -> str | None:
    if not credentials or not accepts_user_keys(provider):
        return None
    return await get_user_key(credentials, provider.id)


async def effective_key(provider: Provider, credentials: str | None) -> str | None:
    """The key a request to this provider is made with, or None for one that takes no key.

    The requester's own key comes first where the provider takes one, then the admin's, unless the
    provider only takes users' keys.
    """
    if key := await user_key(provider, credentials):
        return key
    if provider.credential_mode == "user":
        raise ProviderKeyMissingError(
            provider.id, message=f"Add your API key for {provider.name}"
        )
    return shared_key(provider)


def has_shared_key(provider: Provider) -> bool:
    return bool(shared_key(provider))
