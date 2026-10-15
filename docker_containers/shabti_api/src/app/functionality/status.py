import os
import time
import httpx
from shabti_types import ShabtiError
from shabti_keycloak import server_url
from . import embeddings_fingerprint
from .opensearch import get_client
from .providers.config import LOCAL, Provider, providers_config
from .providers.discovery import request_headers
from .providers.keys import shared_key
from .providers.registry import get_provider, local_url, split_model_id
from .loaders.tika_client import server_endpoint

# these run ahead of the requests that need the service, so a service that has stopped answering
# should cost the caller a few seconds rather than hang them
CHECK_TIMEOUT = 3
# a remote provider is asked on behalf of every status poll from every open page, which shouldn't
# turn into a request each to an API that may be metered
PROVIDER_CHECK_SECONDS = 60

_provider_checks: dict[str, tuple[float, bool]] = {}


async def _answers(url: str, up: tuple[int, ...] = (200,), **kwargs) -> bool:
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, **kwargs) as client:
            return (await client.get(url)).status_code in up
    except Exception:
        return False


async def check_llm():
    return await _answers(local_url("/health"))


async def check_provider(provider: Provider) -> bool:
    if provider.id == LOCAL:
        return await check_llm()
    checked = _provider_checks.get(provider.id)
    if checked and time.monotonic() - checked[0] < PROVIDER_CHECK_SECONDS:
        return checked[1]
    key = shared_key(provider)
    running = await _answers(
        f"{provider.base_url}/models",
        # only users' own keys are used with a provider that has no shared key, so all a check can
        # learn is that it's there, refusing a request without one
        up=(200,) if key else (200, 401, 403),
        headers=request_headers(provider, key),
        follow_redirects=False,
    )
    _provider_checks[provider.id] = (time.monotonic(), running)
    return running


async def check_embeddings() -> bool:
    """Whether the embeddings model can be reached, and still makes the vectors it did."""
    try:
        configured = providers_config().embeddings
        # without a configured model it is whichever local one carries the tag
        provider = get_provider(
            split_model_id(configured.model)[0] if configured else LOCAL
        )
    except ShabtiError:
        return False
    if not await check_provider(provider):
        return False
    await embeddings_fingerprint.verify()
    return not embeddings_fingerprint.is_drifted()


async def check_opensearch():
    # rather than ping, which answers as soon as the node is listening - before the cluster has a
    # manager, when every index request still fails
    try:
        health = await get_client().cluster.health(
            timeout="1s", request_timeout=CHECK_TIMEOUT
        )
        return health["status"] in ("yellow", "green")
    except Exception:
        return False


async def check_tika():
    return await _answers(f"{server_endpoint()}/tika")


async def check_keycloak():
    return await _answers(
        f"{server_url()}/realms/shabti/.well-known/openid-configuration",
        verify=os.getenv("ROOT_CA") or True,
    )
