import json
import re
from shabti_types import (
    ProviderError,
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)

# enough for a provider to say which limit was hit, not enough to pass a whole page on
DETAIL_LIMIT = 300

BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def upstream_detail(body, key: str | None = None) -> str | None:
    """What the provider said went wrong, safe to show whoever made the request.

    Rate limits and quotas are only actionable with the provider's own reason - a per-minute limit
    is waited out, a per-day one isn't - so it is passed on. A provider can echo a credential back,
    so the key the request was made with, and anything sent as a bearer token, is redacted.
    """
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            pass
    if isinstance(body, dict):
        error = body.get("error")
        message = (
            (error.get("message") if isinstance(error, dict) else error)
            or body.get("message")
            or body.get("detail")
        )
    else:
        message = body
    if not isinstance(message, str):
        return None
    text = " ".join(message.split())
    if key:
        text = text.replace(key, "***")
    text = BEARER.sub("Bearer ***", text)
    if len(text) > DETAIL_LIMIT:
        text = text[: DETAIL_LIMIT - 1] + "…"
    return text or None


def by_status(status: int | None, model_name: str) -> ProviderError:
    # by status rather than by any client's exception classes, which don't name every status we
    # report
    if status in (401, 403):
        return ProviderAuthError(model_name, status=status)
    if status == 402:
        return ProviderQuotaError(model_name)
    if status == 404:
        return ProviderNotFoundError(model_name)
    if status == 429:
        return ProviderRateLimitError(model_name)
    return ProviderUnavailableError(model_name)


def provider_error(
    status: int | None,
    model_name: str,
    purpose: str | None = None,
    detail: str | None = None,
) -> ProviderError:
    """The error for a provider's failure, saying which model it was for and why, if known.

    `purpose` is what the model was being used for, so that a chat which failed while its question
    was being embedded doesn't read as the chat model failing. `detail` is from `upstream_detail`.
    """
    error = by_status(status, model_name)
    if purpose and model_name not in error.message:
        error.message += f" ({purpose} model {model_name})"
    if detail:
        error.message += f": {detail}"
    return error
