import asyncio
from shabti_types import Service, ServiceUnavailableError
from ..functionality import status


class RequiredServices:
    """Refuse the request with a 503 naming whichever of these services isn't accepting requests.

    Listed first in a route's dependencies so that it runs ahead of the ones which would otherwise
    fail on the same missing service with something far less clear.
    """

    def __init__(self, *services: Service):
        self.services = services

    async def __call__(self):
        # looked up on the module at call time rather than once here, so a test can patch one
        checks = {
            Service.OPENSEARCH: status.check_opensearch,
            Service.LLM: status.check_llm,
            Service.TIKA: status.check_tika,
            Service.KEYCLOAK: status.check_keycloak,
        }
        results = await asyncio.gather(
            *(checks[service]() for service in self.services)
        )
        missing = [
            service for service, running in zip(self.services, results) if not running
        ]
        if missing:
            raise ServiceUnavailableError(missing)
