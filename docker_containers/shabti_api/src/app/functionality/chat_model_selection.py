from shabti_types import ModelNotFoundError
from .models import (
    ACTIVE_STATUSES,
    active_chat_model,
    default_model_id,
    get_models,
    model_status,
)
from .user_settings import get_user_chat_model, set_user_chat_model

# credentials are None when security is disabled, in which case Shabti is treated as a single
# instance and the loaded model, rather than a stored preference, is the selection


async def chat_model_selection(credentials: str | None) -> str | None:
    return await select_chat_model(credentials, await get_models(tags=["chat"]))


# takes the output of get_models so the caller can reuse a list it has already fetched
async def select_chat_model(credentials: str | None, models) -> str | None:
    if credentials:
        stored = await get_user_chat_model(credentials)
        # the stored model may have been removed from the installation since it was chosen
        if stored and any(x["id"] == stored for x in models["data"]):
            return stored
    elif loaded := active_chat_model(models):
        return loaded
    return default_model_id(models)


async def set_chat_model_selection(credentials: str | None, model_name: str):
    if credentials:
        await set_user_chat_model(credentials, model_name)


# the model a prompt answers with: the one it asks for, or else the requester's selection. each
# prompt names its own rather than using whichever is loaded, so one user can't answer another's
# prompt with the model they switched to
async def resolve_prompt_model(credentials: str | None, requested: str | None) -> str:
    models = await get_models(tags=["chat"])
    model_id = requested or await select_chat_model(credentials, models)
    if not model_id:
        raise ModelNotFoundError(message="No chat model is available")
    model = next((x for x in models["data"] if x["id"] == model_id), None)
    if not model:
        raise ModelNotFoundError(
            model=model_id, message=f"Model {model_id} is not available"
        )
    if model_status(model) not in ACTIVE_STATUSES:
        raise ModelNotFoundError(
            model=model_id, message=f"Model {model_id} is not loaded"
        )
    return model_id
