"""Getting an upload's bytes onto disk, and off it again.

Uploads have to be saved before the POST that carried them returns: Starlette closes `UploadFile`'s
spooled temp file when the request ends, and an ingest now outlives its request. The saved name
doubles as the ingest item's id and as the `binary_path` recorded on the parent document, so there
is only ever one identifier per binary to reconcile.
"""

import os
from dataclasses import dataclass
from typing import BinaryIO
from uuid import uuid4

import aiofiles
import aiofiles.os
from fastapi import UploadFile

from .content_hash import binary_hasher

# a whole upload was read into memory before this: `await f.write(await file.read())`
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SavedUpload:
    # also the ingest item's id and the document's binary_path
    item_id: str
    filename: str | None
    label: str
    # a hash of the bytes, which becomes the document's OpenSearch id so that uploading the same
    # file twice is refused by the database rather than by a check that could race
    binary_hash: str


def files_dir() -> str:
    return os.getenv("SHABTI_FILES_DIR")


def file_path(name: str) -> str:
    return os.path.join(files_dir(), name)


async def discard(name: str) -> None:
    try:
        await aiofiles.os.remove(file_path(name))
    except FileNotFoundError:
        # a binary whose document already claimed it, or one a crash left unwritten
        pass


async def save_uploads(files: list[UploadFile]) -> list[SavedUpload]:
    saved: list[SavedUpload] = []
    try:
        for file in files:
            name = uuid4().hex
            await file.seek(0)
            # hashed in the loop that was already reading these bytes, so identifying the upload
            # costs no additional pass over it
            digest = binary_hasher()
            async with aiofiles.open(file_path(name), "wb") as out:
                while chunk := await file.read(CHUNK_BYTES):
                    digest.update(chunk)
                    await out.write(chunk)
            saved.append(
                SavedUpload(
                    item_id=name,
                    filename=file.filename,
                    label=file.filename or "upload",
                    binary_hash=digest.hexdigest(),
                )
            )
    except Exception:
        # nothing owns these yet, so a partial batch is ours to clean up
        for entry in saved:
            await discard(entry.item_id)
        raise
    return saved


def save_binary(source: BinaryIO, max_bytes: int) -> tuple[str, int, str]:
    """Copy an open binary to a new saved file, synchronously. Call from a thread.

    Returns the saved name, its size, and a hash of its bytes. Refuses to write more than
    `max_bytes`, deleting the partial file, so expanding an archive can't fill the disk.
    """
    name = uuid4().hex
    written = 0
    digest = binary_hasher()
    try:
        with open(file_path(name), "wb") as out:
            while chunk := source.read(CHUNK_BYTES):
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("expanded size limit exceeded")
                digest.update(chunk)
                out.write(chunk)
    except Exception:
        try:
            os.remove(file_path(name))
        except FileNotFoundError:
            pass
        raise
    return name, written, digest.hexdigest()
