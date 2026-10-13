from shabti_api_client import ShabtiRequestError
from shabti_types import ShabtiError


def request_error_text(error: Exception) -> str:
    """What the API said went wrong with a request, for showing to whoever made it."""
    if isinstance(error, ShabtiRequestError):
        body = error.message["message"]
        if isinstance(body, dict):
            return str(body.get("message") or body.get("detail") or body)
        return str(body)
    # the errors raise_error turns into types of their own
    if isinstance(error, ShabtiError):
        return error.message
    return str(error)
