"""Users' own keys for the providers that take them, kept encrypted in their Keycloak profile.

Each key is one value of a multivalued user attribute, `<provider_id>:<version>:<nonce>:<sealed>`,
sealed with AES-256-GCM under the installation's key, version `version` of `secrets_key()`. The
user, provider and version are bound in as associated data, so a value copied to another user or
provider, or relabelled with another version, won't decrypt. Only admins can see the attribute, and
it only ever holds the ciphertext.

Nothing here puts a key, decrypted or not, into a message or a log.
"""

import base64
import logging
import os
import time
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ..user_settings import (
    PROVIDER_KEYS_ATTRIBUTE,
    get_attribute,
    update_attribute,
    user_id,
)
from .secret_files import secrets_key

# a key is needed on every listing and prompt, and each read is a round trip to Keycloak
CACHE_SECONDS = 60
NONCE_BYTES = 12

_cache: dict[tuple[str, str], tuple[float, str | None]] = {}


class UnreadableKeyError(Exception):
    """A stored key that can't be decrypted: altered, moved, or sealed under a version that has
    since been removed. The user has to add it again."""


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def associated_data(sub: str, provider_id: str, version: str) -> bytes:
    return f"{sub}|{provider_id}|{version}".encode()


def record_provider(record: str) -> str:
    return record.split(":", 1)[0]


def record_version(record: str) -> str:
    parts = record.split(":")
    return parts[1] if len(parts) == 4 else ""


def encrypt(sub: str, provider_id: str, key: str) -> str:
    version, keys = secrets_key()
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(keys[version]).encrypt(
        nonce, key.encode(), associated_data(sub, provider_id, version)
    )
    return f"{provider_id}:{version}:{encode(nonce)}:{encode(sealed)}"


def decrypt(sub: str, record: str) -> str:
    parts = record.split(":")
    if len(parts) != 4:
        raise UnreadableKeyError()
    provider_id, version, nonce, sealed = parts
    kek = secrets_key()[1].get(version)
    if kek is None:
        raise UnreadableKeyError()
    try:
        return (
            AESGCM(kek)
            .decrypt(
                decode(nonce),
                decode(sealed),
                associated_data(sub, provider_id, version),
            )
            .decode()
        )
    except (InvalidTag, ValueError):
        raise UnreadableKeyError() from None


def needs_rewrap(record: str) -> bool:
    """Whether the record was sealed under an older version than new ones are."""
    return record_version(record) != secrets_key()[0]


def replacing(provider_id: str, record: str | None):
    """An update for the attribute that swaps the provider's record for `record`, or drops it."""

    def update(records: list[str]) -> list[str]:
        kept = [x for x in records if record_provider(x) != provider_id]
        return kept + [record] if record else kept

    return update


def unreadable(sub: str, provider_id: str):
    logging.getLogger("shabti").warning(
        "the stored %s key of user %s could not be decrypted", provider_id, sub
    )


async def get_user_key(credentials: str, provider_id: str) -> str | None:
    sub = await user_id(credentials)
    cached = _cache.get((sub, provider_id))
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    records = await get_attribute(sub, PROVIDER_KEYS_ATTRIBUTE)
    record = next((x for x in records if record_provider(x) == provider_id), None)
    key = None
    if record:
        try:
            key = decrypt(sub, record)
        except UnreadableKeyError:
            # as though there were none, so the user is asked to add it again
            unreadable(sub, provider_id)
        else:
            # re-encrypted as it's used, so a rotation doesn't depend on every key being rewrapped
            # at once
            if needs_rewrap(record):
                await update_attribute(
                    sub,
                    PROVIDER_KEYS_ATTRIBUTE,
                    replacing(provider_id, encrypt(sub, provider_id, key)),
                )
    _cache[(sub, provider_id)] = (time.monotonic(), key)
    return key


async def list_user_keys(credentials: str) -> dict[str, str]:
    """The last four characters of each of the user's keys, by provider id."""
    sub = await user_id(credentials)
    found = {}
    for record in await get_attribute(sub, PROVIDER_KEYS_ATTRIBUTE):
        provider_id = record_provider(record)
        try:
            found[provider_id] = decrypt(sub, record)[-4:]
        except UnreadableKeyError:
            unreadable(sub, provider_id)
    return found


async def set_user_key(credentials: str, provider_id: str, key: str):
    sub = await user_id(credentials)
    await update_attribute(
        sub,
        PROVIDER_KEYS_ATTRIBUTE,
        replacing(provider_id, encrypt(sub, provider_id, key)),
    )
    _cache.pop((sub, provider_id), None)


async def delete_user_key(credentials: str, provider_id: str):
    sub = await user_id(credentials)
    await update_attribute(sub, PROVIDER_KEYS_ATTRIBUTE, replacing(provider_id, None))
    _cache.pop((sub, provider_id), None)
