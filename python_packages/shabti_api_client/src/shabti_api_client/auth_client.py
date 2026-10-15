from typing import Any
import httpx
from ssl import SSLContext
from urllib.parse import urljoin
from keycloak import KeycloakOpenID
from asyncio import create_task, Task
from .exceptions import ShabtiRequestError
from .codes import EXPECTED_CODES
from shabti_types import (
    AuthzCollectionInfo,
)
from .base_client import POOL_LIMITS, BaseShabtiClient, send_with_retry
from .raise_error import raise_error


class ShabtiAuthenticationError(ShabtiRequestError):
    pass


class ShabtiTokenExpiredError(ShabtiRequestError):
    pass


class ShabtiAuthorizationClient(BaseShabtiClient):
    def __init__(
        self,
        server_url: str,
        token: Any,
        keycloak_client: KeycloakOpenID,
        verify: SSLContext | None = None,
    ):
        self.server_url = server_url
        self.httpx_client = httpx.AsyncClient(
            verify=verify, timeout=None, limits=POOL_LIMITS
        )
        self.token = token
        self.keycloak_client = keycloak_client
        self.refresh_task: Task | None = None

    async def _make_request(
        self, method, url, json=None, files=None, stream=False, params: dict = None
    ) -> httpx.Response:
        async def make_request(token):
            headers = (
                {"Authorization": f"Bearer {token['access_token']}"} if token else None
            )

            # built inside `send` rather than once outside it, so a retry is a fresh request
            async def send():
                request = self.httpx_client.build_request(
                    method=method,
                    url=urljoin(self.server_url, url),
                    headers=headers,
                    json=json,
                    files=files,
                    params=params,
                )
                return await self.httpx_client.send(request, stream=stream)

            # the status handling stays out here: a status code is never a reason to re-send. A
            # streaming send returns once the headers are read, before any body is consumed, so a
            # retry is safe for those too
            response = await send_with_retry(method, send)
            if response.status_code == 401:
                raise ShabtiTokenExpiredError(status_code=401)
            if response.status_code == 403:
                raise ShabtiAuthenticationError(status_code=403)
            if response.status_code not in EXPECTED_CODES:
                await response.aread()
                raise_error(response)
            return response

        token = self.token
        task = create_task(make_request(token))

        try:
            if self.refresh_task:
                await self.refresh_task
            result = await task
            return result
        except ShabtiTokenExpiredError:
            # if the token being used is still the same as the stored one, it probably expired
            if token == self.token:
                # if we're not already refreshing, we should launch the refresh task
                if self.refresh_task is None:

                    async def do_refresh():
                        self.token = await self.keycloak_client.a_refresh_token(
                            self.token["refresh_token"]
                        )

                    self.refresh_task = create_task(do_refresh())

                    # unset refresh task when done
                    def on_done(_):
                        self.refresh_task = None

                    self.refresh_task.add_done_callback(on_done)
                # once we've ensured a refresh task is running, wait for it to complete
                await self.refresh_task
            # if not it has probably been refreshed and we're good to go
            # try to rerun using current token
            return await self._make_request(
                method=method,
                url=url,
                json=json,
                files=files,
                stream=stream,
                params=params,
            )

    async def _stream_request(
        self, method, url, json=None, files=None, params: dict = None
    ):
        response = await self._make_request(
            method=method, url=url, json=json, files=files, stream=True, params=params
        )
        # closed however the iteration ends: detaching from an ingest partway through is an
        # ordinary thing to do now, and a `break` used to leak the connection
        try:
            async for line in response.aiter_lines():
                yield line
        finally:
            await response.aclose()

    async def create_collection(
        self, collection_name: str, location: str, owner_username: str | None = None
    ) -> str:
        response = await self._make_request(
            "POST",
            "collections",
            {
                "collection_name": collection_name,
                "location": location,
                "owner_username": owner_username,
            },
        )
        return response.json()["collection_id"]

    async def get_collections(self):
        response = await self._make_request("GET", "collections")
        return [AuthzCollectionInfo(**item) for item in response.json()]

    async def get_user_info(self):
        response = await self._make_request("GET", "/user_info")
        return response.json()

    async def get_permissions(self):
        response = await self._make_request("GET", "/permissions")
        return set(response.json())

    async def get_collection_scopes(self, collection_id: str):
        response = await self._make_request(
            "GET", f"/collections/{collection_id}/scopes"
        )
        return set(response.json())

    # the user's own keys for the providers that take them. only the last four characters of each
    # are ever sent back, by provider id
    async def get_api_keys(self) -> dict[str, str]:
        response = await self._make_request("GET", "providers/credentials")
        return response.json()

    async def set_api_key(self, provider_id: str, key: str) -> dict[str, str]:
        response = await self._make_request(
            "PUT", f"providers/{provider_id}/credential", {"key": key}
        )
        return response.json()

    async def delete_api_key(self, provider_id: str) -> dict[str, str]:
        response = await self._make_request(
            "DELETE", f"providers/{provider_id}/credential"
        )
        return response.json()
