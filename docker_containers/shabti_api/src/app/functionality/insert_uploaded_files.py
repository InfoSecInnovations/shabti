import asyncio
import os
import zipfile
from collections.abc import AsyncGenerator
from contextlib import suppress

from isi_util.stream_pool import (
    PoolValue,
    StreamPool,
    StreamStoppedError,
)
from keycloak import KeycloakAuthenticationError, KeycloakPostError
from shabti_types import (
    DocumentIngestError,
    DocumentIngestInfo,
    DuplicateDocumentError,
    EmptyDocumentError,
    UnsupportedFileError,
    UserInfo,
)

from .ingest_events import (
    IngestEvent,
    ItemExpanded,
    ItemFailed,
    ItemProgress,
    ItemQueued,
)
from .ingesting import insert_document
from .loading import load_file
from .opensearch import get_client
from .save_uploads import SavedUpload, discard, file_path, save_binary
from .settings import setting

# for debugging purposes we can set this to True and try to get more information about why an upload is failing
# we make sure this doesn't accidentally get enabled in production
RAISE_EXCEPTIONS = os.getenv("ENVIRONMENT") == "development" and False


class DocumentLoadError(Exception):
    """A file could not be read at all, as opposed to failing later in the ingest.

    The fallback zip probe hangs off this rather than off any failure, so an embeddings outage or
    an OpenSearch error part way through a document is not mistaken for an archive. Detection
    proper is `ArchiveDetectedError` below; this covers only a zip variant `zipfile` can read and
    Tika cannot.
    """


class ArchiveDetectedError(Exception):
    """An upload is a plain zip, so its members are ingested in its place.

    Raised by the job instead of loading, and acted on by the reader loop: expansion has to be
    submitted from there, which is the one place `pool.submit` while the pool is being read is
    trivially race free. A detection rather than a failure, because Tika parses an archive
    perfectly well - into a single document holding every member's text concatenated, with the
    archive's own filename - so waiting for something to go wrong would never expand anything.
    """


# the entries Tika's own zip container detection keys on: an OPC part map (docx, xlsx, pptx), an ODF
# or epub mimetype declaration, an ODF manifest, a jar manifest, an epub root file map. Content
# derived rather than extension derived, because the filename is whatever the client sent
PACKAGE_MARKERS = frozenset(
    {
        "[Content_Types].xml",
        "mimetype",
        "META-INF/manifest.xml",
        "META-INF/MANIFEST.MF",
        "META-INF/container.xml",
    }
)


def archive_member_names(name: str) -> list[str] | None:
    """The member names of a plain zip, or None for anything else. Call from a thread.

    Only the end of central directory and the central directory itself are read - no member is
    decompressed and nothing is sent to Tika - so this costs the same on a 200MB archive as on a
    tiny one. A docx, odt, epub or jar is a zip too, and expanding one would replace the document
    with its own internal XML parts, so any package marker means the file belongs to a parser
    rather than to us.
    """
    path = file_path(name)
    if not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    if PACKAGE_MARKERS.intersection(names):
        return None
    return names


def is_plain_archive(name: str) -> bool:
    return archive_member_names(name) is not None


def load_saved(saved: SavedUpload):
    # the handle can be closed here because TikaFileLoader is eager: it reads the whole file, parses
    # the response and builds every page before `insert` asks for the first one
    with open(file_path(saved.item_id), "rb") as f:
        return load_file(f, saved.filename)


def expand_zip(name: str, max_members: int, max_bytes: int) -> list[SavedUpload]:
    """Stream an archive's members out to their own saved files. Call from a thread.

    Members go to disk one at a time rather than into a list of BytesIO holding the whole archive,
    and each becomes an ingest item of its own so they share the pool's limit.
    """
    if archive_member_names(name) is None:
        # not a zip, or a docx, odt, epub or jar whose parts belong to Tika rather than to us
        return []
    members: list[SavedUpload] = []
    remaining = max_bytes
    try:
        with zipfile.ZipFile(file_path(name)) as archive:
            # zip info includes directories, but we don't want to try to read those!
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if len(entries) > max_members:
                raise ValueError(f"archive holds more than {max_members} files")
            for info in entries:
                with archive.open(info) as source:
                    member, written, digest = save_binary(source, remaining)
                remaining -= written
                filename = os.path.basename(info.filename)
                members.append(
                    SavedUpload(
                        item_id=member,
                        filename=filename,
                        label=filename or "upload",
                        binary_hash=digest,
                    )
                )
    except Exception:
        # a member that blew the byte cap deletes only its own partial file, and the caller throws
        # this list away, so the members written before it would be left on disk for ever
        for member in members:
            with suppress(FileNotFoundError):
                os.remove(file_path(member.item_id))
        raise
    return members


async def insert_uploaded_files(
    actor: UserInfo | None,
    collection_id: str,
    saved: list[SavedUpload],
    pool: StreamPool[DocumentIngestInfo],
) -> AsyncGenerator[IngestEvent, None]:
    """Ingest a batch of already saved uploads, up to the pool's limit at a time.

    Binaries are tracked in `unclaimed` until the document that owns one reports `complete`, and
    whatever is left is deleted on the way out - unsupported, failed, cancelled, never started, or
    an archive whose members now carry the content. That is the same end state as ingesting
    serially, where nothing was written until a file had already loaded.
    """
    unclaimed = {entry.item_id: entry for entry in saved}
    labels = {entry.item_id: entry.label for entry in saved}

    def job(entry: SavedUpload):
        async def factory():
            # a get by the very id the document would be given, so a file the collection already
            # holds costs one round trip rather than a Tika parse and a whole embedding pass. an
            # optimisation only - `op_type=create` in `insert` is what makes this safe against two
            # copies arriving at once, so this racing costs nothing
            if await get_client().exists(index=collection_id, id=entry.binary_hash):
                raise DuplicateDocumentError(
                    source=entry.label,
                    existing_document_id=entry.binary_hash,
                    message=(
                        f"{entry.label} is already in this collection as document "
                        f"{entry.binary_hash}"
                    ),
                )
            # in the slot and in a thread like the parse below: reading a central directory is cheap
            # but it is still blocking file IO, and it has to happen per item so that a member which
            # is itself a zip gets the same treatment
            if await asyncio.to_thread(is_plain_archive, entry.item_id):
                raise ArchiveDetectedError(entry.label)
            try:
                # Tika parsing is blocking and takes tens of seconds on a large PDF, which would
                # otherwise stall every other request on the event loop for that whole time
                stream = await asyncio.to_thread(load_saved, entry)
            except Exception as e:
                raise DocumentLoadError(entry.label) from e
            if not stream:
                raise UnsupportedFileError(
                    message="No content was able to be loaded from the file",
                    filename=entry.filename,
                )
            return insert_document(
                actor, collection_id, stream, entry.item_id, entry.binary_hash
            )

        return factory

    def failure(item_id: str, error: str, message: str) -> ItemFailed:
        return ItemFailed(
            item_id,
            DocumentIngestError(
                error=error,
                message=message,
                filename=labels.get(item_id),
                label=labels.get(item_id),
            ),
        )

    async def drop(item_id: str) -> None:
        entry = unclaimed.pop(item_id, None)
        if entry is not None:
            await discard(entry.item_id)

    try:
        async with pool:
            for entry in saved:
                pool.submit(job(entry), key=entry.item_id)
            async for result in pool.results():
                if isinstance(result, PoolValue):
                    yield ItemProgress(result.key, result.value)
                    if result.value.complete:
                        # the document owns its binary from here, so it is no longer ours to delete
                        unclaimed.pop(result.key, None)
                    continue
                error = result.error
                if error is None or isinstance(error, StreamStoppedError):
                    # a clean finish claimed its binary above; a stop leaves it to the cleanup below
                    continue
                if isinstance(error, (KeycloakPostError, KeycloakAuthenticationError)):
                    # an auth failure is not this file's problem, so it ends the whole batch:
                    # leaving the loop is what tears the pool down
                    raise error
                if isinstance(error, UnsupportedFileError):
                    yield failure(result.key, "UnsupportedFileError", error.message)
                    await drop(result.key)
                    continue
                if isinstance(error, EmptyDocumentError):
                    yield failure(result.key, "EmptyDocumentError", error.message)
                    await drop(result.key)
                    continue
                if isinstance(error, DuplicateDocumentError):
                    # before the archive branch below, so a duplicate is never mistaken for a zip
                    # variant Tika could not read. dropping the binary is right either way: this
                    # document was rolled back, and the one that survives has a binary of its own
                    yield failure(result.key, "DuplicateDocumentError", error.message)
                    await drop(result.key)
                    continue
                members: list[SavedUpload] = []
                expansion_error: Exception | None = None
                if (
                    # the archive gate is the normal path; the load failure probe stays as a
                    # fallback for a zip variant `zipfile` reads but Tika cannot, and is safe
                    # because `expand_zip` applies the same package exclusion
                    isinstance(error, (ArchiveDetectedError, DocumentLoadError))
                    and result.key in unclaimed
                ):
                    try:
                        members = await asyncio.to_thread(
                            expand_zip,
                            result.key,
                            setting("SHABTI_INGEST_MAX_ZIP_MEMBERS"),
                            setting("SHABTI_INGEST_MAX_ZIP_BYTES"),
                        )
                    except Exception as e:
                        # over a cap, a corrupt member, or not an archive after all
                        expansion_error = e
                if members:
                    # submitted from the reader, which is where submit-while-running is trivially
                    # race free, so members join the same limit as everything else rather than
                    # nesting a second pool inside this one's slot
                    for member in members:
                        unclaimed[member.item_id] = member
                        labels[member.item_id] = member.label
                        pool.submit(job(member), key=member.item_id)
                        yield ItemQueued(member.item_id, member.label)
                    # after the members are queued, so the archive is never briefly the only thing
                    # a client can see having been accounted for
                    yield ItemExpanded(result.key, len(members))
                    # the archive's own bytes are done with: its members carry the content now
                    await drop(result.key)
                    continue
                if isinstance(error, ArchiveDetectedError):
                    # recognised as an archive and then not expanded, so the generic message below
                    # would be the very symptom this replaced: say what stopped it instead
                    yield failure(
                        result.key,
                        "UnsupportedFileError",
                        f"Archive {labels.get(result.key)} could not be expanded: "
                        f"{expansion_error}"
                        if expansion_error
                        else f"Archive {labels.get(result.key)} holds no files",
                    )
                    await drop(result.key)
                    continue
                if RAISE_EXCEPTIONS:
                    raise error
                yield failure(
                    result.key,
                    "UnsupportedFileError",
                    f"File {labels.get(result.key)} could not be loaded",
                )
                await drop(result.key)
    finally:
        for name in list(unclaimed):
            await discard(name)
