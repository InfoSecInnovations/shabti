from shabti_types import (
    ProviderError,
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)


# by status rather than by any client's exception classes, which don't name every status we report.
# nothing from the upstream response is passed on, as a provider can echo credentials back
def provider_error(status: int | None, model_name: str) -> ProviderError:
    if status in (401, 403):
        return ProviderAuthError(model_name, status=status)
    if status == 402:
        return ProviderQuotaError(model_name)
    if status == 404:
        return ProviderNotFoundError(model_name)
    if status == 429:
        return ProviderRateLimitError(model_name)
    return ProviderUnavailableError(model_name)
