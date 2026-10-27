"""Deleting a collection tidies its stored files, but a file that is already gone isn't an error.

The files folder can be cleared out from under Shabti (a throwaway container, or an install whose
folder was wiped), and the indices are deleted before the files, so a missing file must not fail
the request after the collection is already gone.
"""

import pytest

from shabti_types import CollectionInfo
from ...src.app.functionality import document_collections


@pytest.fixture
def stubbed_collection(monkeypatch, tmp_path):
    """Stubs every call delete_collection makes, leaving only the file removal real."""
    info = CollectionInfo(collection_id="c1", collection_name="one")

    async def get_collection_info(collection_id):
        return info

    async def freeze_collection_and_get_file_paths(collection_id):
        return ["present.txt", "missing.txt"]

    async def noop(*args, **kwargs):
        return None

    async def reset_if_unused():
        return None

    monkeypatch.setattr(
        document_collections, "get_collection_info", get_collection_info
    )
    monkeypatch.setattr(document_collections, "auth_enabled", lambda: False)
    monkeypatch.setattr(document_collections, "delete_index_mapping", noop)
    monkeypatch.setattr(
        document_collections,
        "freeze_collection_and_get_file_paths",
        freeze_collection_and_get_file_paths,
    )
    monkeypatch.setattr(document_collections, "delete_collection_indices", noop)
    monkeypatch.setattr(
        document_collections.embeddings_fingerprint, "reset_if_unused", reset_if_unused
    )
    monkeypatch.setattr(document_collections, "log_user_action", noop)
    monkeypatch.setenv("SHABTI_FILES_DIR", str(tmp_path))
    return info


async def test_delete_collection_tolerates_a_missing_file(
    monkeypatch, tmp_path, stubbed_collection
):
    (tmp_path / "present.txt").write_text("still here")

    info = await document_collections.delete_collection(None, "c1")

    assert info == stubbed_collection
    assert not (tmp_path / "present.txt").exists()


async def test_delete_collection_still_raises_other_removal_errors(
    monkeypatch, tmp_path, stubbed_collection
):
    (tmp_path / "present.txt").write_text("still here")

    async def refuse(path):
        raise PermissionError(path)

    # monkeypatch restores aiofiles.os.remove after the test, so nothing else sees the stub
    monkeypatch.setattr(document_collections.aiofiles.os, "remove", refuse)

    with pytest.raises(PermissionError):
        await document_collections.delete_collection(None, "c1")
