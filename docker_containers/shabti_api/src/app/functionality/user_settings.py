from collections.abc import Callable
from shabti_keycloak import get_keycloak_admin_client, get_token_info

# Keycloak drops user attributes that aren't declared in the realm's user profile, so these are
# declared in the realm import file
CHAT_MODEL_ATTRIBUTE = "shabti_chat_model"
# one encrypted value per provider, see providers/credentials.py
PROVIDER_KEYS_ATTRIBUTE = "shabti_provider_keys"


async def user_id(credentials: str) -> str:
    return (await get_token_info(credentials))["sub"]


async def get_attribute(sub: str, name: str) -> list[str]:
    user = await get_keycloak_admin_client().a_get_user(sub)
    return user.get("attributes", {}).get(name) or []


async def update_attribute(
    sub: str, name: str, update: Callable[[list[str]], list[str]]
):
    """Replace one attribute's values with what `update` makes of the current ones."""
    admin_client = get_keycloak_admin_client()
    user = await admin_client.a_get_user(sub)
    # updating a user replaces its whole attribute map, so we merge into what's already there. the
    # rest of the user goes back too: with a user profile, Keycloak takes a name or an email left
    # out of the update as cleared, and won't let the user log in until they fill it in again
    attributes = user.get("attributes", {})
    attributes[name] = update(attributes.get(name) or [])
    await admin_client.a_update_user(sub, {**user, "attributes": attributes})


async def get_user_chat_model(credentials: str) -> str | None:
    values = await get_attribute(await user_id(credentials), CHAT_MODEL_ATTRIBUTE)
    return values[0] if values else None


async def set_user_chat_model(credentials: str, model_name: str):
    await update_attribute(
        await user_id(credentials), CHAT_MODEL_ATTRIBUTE, lambda _: [model_name]
    )
