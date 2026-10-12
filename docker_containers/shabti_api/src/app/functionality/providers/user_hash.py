import hashlib
from ..user_settings import user_id
from .config import LOCAL, Provider
from .secret_files import instance_salt


async def provider_user(provider: Provider, credentials: str | None) -> str | None:
    """Who a request to a remote provider is for, so it can tell abuse by one user from the rest.

    Salted per installation, so the provider can't match it to the same Keycloak user elsewhere.
    Nothing with security disabled, as every request comes from the same anonymous user.
    """
    if provider.id == LOCAL or not credentials:
        return None
    salt = instance_salt()
    if not salt:
        return None
    return hashlib.sha256((salt + await user_id(credentials)).encode()).hexdigest()
