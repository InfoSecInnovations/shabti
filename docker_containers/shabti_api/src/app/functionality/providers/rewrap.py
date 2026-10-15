"""Re-encrypts every user's provider keys under the newest version of the installation's key.

Run by the configurator after it rotates the key, see `rewrap_provider_keys.py`. Keys are also
re-encrypted one at a time as they are used, so this only hurries along the ones nobody is using.
"""

from shabti_keycloak import get_keycloak_admin_client
from ..user_settings import PROVIDER_KEYS_ATTRIBUTE
from .credentials import (
    UnreadableKeyError,
    decrypt,
    encrypt,
    needs_rewrap,
    record_provider,
)


def rewrap_records(sub: str, records: list[str]) -> tuple[list[str], int, int]:
    """The records re-encrypted where needed, with how many were and how many couldn't be read.

    An unreadable record is kept as it is rather than lost, in case an older key is restored.
    """
    rewrapped = []
    changed = failed = 0
    for record in records:
        if not needs_rewrap(record):
            rewrapped.append(record)
            continue
        try:
            key = decrypt(sub, record)
        except UnreadableKeyError:
            rewrapped.append(record)
            failed += 1
            continue
        rewrapped.append(encrypt(sub, record_provider(record), key))
        changed += 1
    return rewrapped, changed, failed


def rewrap_all():
    client = get_keycloak_admin_client()
    users = changed = failed = 0
    for listed in client.get_users({}):
        # fetched one by one, as the merge has to start from the whole attribute map
        user = client.get_user(listed["id"])
        attributes = user.get("attributes", {})
        records = attributes.get(PROVIDER_KEYS_ATTRIBUTE)
        if not records:
            continue
        attributes[PROVIDER_KEYS_ATTRIBUTE], user_changed, user_failed = rewrap_records(
            user["id"], records
        )
        if user_changed:
            # the whole user, see user_settings.update_attribute
            client.update_user(user["id"], {**user, "attributes": attributes})
            users += 1
        changed += user_changed
        failed += user_failed
    print(f"Re-encrypted {changed} provider keys belonging to {users} users")
    if failed:
        print(
            f"{failed} provider keys could not be decrypted and were left as they were"
        )
