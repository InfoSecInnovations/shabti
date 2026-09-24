from httpx import Response
from .exceptions import ShabtiRequestError
from shabti_types import (
    CollectionExistsError,
    IngestNotFoundError,
    ServiceUnavailableError,
)


def raise_error(response: Response):
    body = response.json()
    if response.status_code >= 400:  # 400 and above are errors
        if "error_type" in body:
            if body["error_type"] == "CollectionExistsError":
                raise CollectionExistsError(
                    body["collection_name"],
                    body["message"],
                    body["location"] if "location" in body else None,
                )
            # an ingest is pruned once it's old enough, and one belonging to somebody else is a 404
            # rather than a 403, so this is the ordinary outcome of attaching to a stale id
            if body["error_type"] == "IngestNotFoundError":
                raise IngestNotFoundError(body["ingest_id"], body["message"])
            if body["error_type"] == "ServiceUnavailableError":
                raise ServiceUnavailableError(body["services"], body["message"])
    raise ShabtiRequestError(status_code=response.status_code, message=body)
