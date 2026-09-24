import os
import httpx
from shabti_keycloak import server_url
from .opensearch import get_client
from .models import llm_url
from .loaders.tika_client import server_endpoint

# these run ahead of the requests that need the service, so a service that has stopped answering
# should cost the caller a few seconds rather than hang them
CHECK_TIMEOUT = 3


async def _answers(url: str, **kwargs) -> bool:
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, **kwargs) as client:
            return (await client.get(url)).status_code == 200
    except Exception:
        return False


async def check_llm():
    return await _answers(llm_url("/health"))


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
