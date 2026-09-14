import logging
from shabti_keycloak import get_token_info
from shabti_types import UserInfo
from shabti_util import auth_enabled
import os


def logging_enabled():
    return os.getenv("SHABTI_LOGGING_ENABLED") == "True"


async def get_actor(token) -> UserInfo | None:
    """Who a request is on behalf of, decoded once so the token itself needn't be held onto.

    An ingest outlives its request now, and there is no reason to keep a user's bearer token in
    process memory for the hours a crawl can take.
    """
    if token is None:
        return None
    token_info = await get_token_info(token)
    # `name` is the claim the audit log has always used, but it is optional and a service account
    # token does without it, so fall back rather than failing the request that carries one
    username = (
        token_info.get("name")
        or token_info.get("preferred_username")
        or token_info["sub"]
    )
    return UserInfo(username=username, user_id=token_info["sub"])


def log_user_action_as(actor: UserInfo | None, action, message, **kwargs):
    """Write one audit entry, attributed to `actor` if there is one.

    An unsecured instance has nobody to attribute an action to, so the entry goes in without a
    user rather than every caller needing a second, near-identical logging call for that case.
    """
    if not logging_enabled():
        return
    logger = logging.getLogger("shabti")
    if actor is None and auth_enabled():
        # a secured instance should never produce an unattributed entry, and one that looks
        # ordinary is the worst kind of audit log defect. warn rather than raise: dropping the
        # call would lose the action as well as the attribution
        logger.warning(
            "audit entry with no actor on a secured instance", extra={"action": action}
        )
    user = {"user": {"name": actor.username, "user_id": actor.user_id}} if actor else {}
    logger.info(message, extra={"action": action, **user, **kwargs})


async def log_user_action(token, action, message, **kwargs):
    # checked here as well as in log_user_action_as so a disabled log never decodes the token
    if not logging_enabled():
        return
    log_user_action_as(await get_actor(token), action, message, **kwargs)
