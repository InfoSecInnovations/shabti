"""The parts of the Tika server REST API Shabti uses.

Our own rather than tika-python or tika-client: both were written against 3.x, which picked the
output format from the `Accept` header and took parser settings as `X-Tika-*` headers, and 4.x
dropped both - parser settings live in the server's config file now (see tika-config.json).
"""

import os
import requests


class TikaError(Exception):
    def __init__(self, http_status: int, tika_status: str | None, message: str):
        super().__init__(message)
        self.http_status = http_status
        self.tika_status = tika_status
        self.message = message


def server_endpoint() -> str:
    return os.getenv("TIKA_SERVER_ENDPOINT", "http://localhost:9998").rstrip("/")


def raise_for_status(response: requests.Response):
    """Turn an error response into a TikaError.

    4.x answers a failed parse with a JSON body carrying a `status` (TIMEOUT, OOM,
    CLIENT_UNAVAILABLE_WITHIN_MS...) and usually a `message`, but a request body over
    `maxRequestSizeBytes` is refused by a filter with plain text before any of that runs.
    """
    if response.status_code < 400:
        return
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and "status" in body:
        raise TikaError(
            response.status_code, body["status"], body.get("message") or body["status"]
        )
    raise TikaError(response.status_code, None, response.text)


def recursive_metadata(data: bytes, handler: str = "xml") -> list[dict]:
    """`/rmeta/{handler}`: a metadata dict per resource, the container first.

    Each entry's content is under `tk:content`, rendered by the named handler. A parse that failed
    part way still answers 200, with the exception under `tk:exception:container-exception`
    alongside whatever was extracted.
    """
    response = requests.put(
        f"{server_endpoint()}/rmeta/{handler}",
        data=data,
        headers={"Accept": "application/json"},
        # a large PDF takes tens of seconds, and the server's own timeouts are what bound a parse
        timeout=None,
    )
    raise_for_status(response)
    return response.json()
