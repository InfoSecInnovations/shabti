from shabti_types import ModelNotFoundError, ProviderModelInfo
from .models import ACTIVE_STATUSES, default_model_id, get_models
from .providers.config import LOCAL, providers_config
from .providers.discovery import list_all_models
from .providers.keys import effective_key, user_keys_enabled
from .providers.registry import ChatModel, get_provider, qualify, split_model_id
from .user_settings import get_user_chat_model, set_user_chat_model

# credentials are None when security is disabled, in which case Shabti is treated as a single
# instance and the loaded model, rather than a stored preference, is the selection. every id here
# is qualified with its provider


async def chat_model_selection(credentials: str | None) -> str | None:
    return await select_chat_model(
        credentials, await list_all_models("chat", credentials)
    )


# takes the output of list_all_models so the caller can reuse a list it has already fetched
async def select_chat_model(
    credentials: str | None, models: list[ProviderModelInfo]
) -> str | None:
    ids = {x.id for x in models}
    if credentials:
        stored = await get_user_chat_model(credentials)
        # the stored model may have been removed from the installation since it was chosen
        if stored in ids:
            return stored
    elif loaded := next(
        (
            x.id
            for x in models
            if x.provider_id == LOCAL and x.status in ACTIVE_STATUSES
        ),
        None,
    ):
        return loaded
    default = providers_config().default_chat_model
    if default in ids:
        return default
    # the listing doesn't carry llama.cpp's tags, so its default is only asked for when needed
    if any(x.provider_id == LOCAL for x in models):
        if local_default := default_model_id(await get_models(tags=["chat"])):
            return qualify(LOCAL, local_default)
    return models[0].id if models else None


async def set_chat_model_selection(credentials: str | None, model_name: str):
    if credentials:
        models = await list_all_models("chat", credentials)
        if not any(x.id == model_name for x in models):
            raise ModelNotFoundError(
                model=model_name, message=f"Model {model_name} is not available"
            )
        await set_user_chat_model(credentials, model_name)


# the model a prompt answers with: the one it asks for, or else the requester's selection. each
# prompt names its own rather than using whichever is loaded, so one user can't answer another's
# prompt with the model they switched to
async def resolve_prompt_model(
    credentials: str | None, requested: str | None
) -> ChatModel:
    models = await list_all_models("chat", credentials)
    model_id = requested or await select_chat_model(credentials, models)
    if not model_id:
        raise ModelNotFoundError(message="No chat model is available")
    model = next((x for x in models if x.id == model_id), None)
    if not model:
        await require_user_key(credentials, model_id)
        raise ModelNotFoundError(
            model=model_id, message=f"Model {model_id} is not available"
        )
    # a remote model is ready whenever its provider lists it
    if model.provider_id == LOCAL and model.status not in ACTIVE_STATUSES:
        raise ModelNotFoundError(
            model=model_id, message=f"Model {model_id} is not loaded"
        )
    return ChatModel(provider=get_provider(model.provider_id), model_id=model.model_id)


async def require_user_key(credentials: str | None, model_id: str):
    """Raises the reason a model is missing when it is the requester's key, rather than leaving it
    as "not available": a provider that only takes users' own keys isn't listed for one without."""
    if not user_keys_enabled():
        return
    try:
        provider = get_provider(split_model_id(model_id)[0])
    except ModelNotFoundError:
        return
    if provider.credential_mode == "user":
        await effective_key(provider, credentials)
