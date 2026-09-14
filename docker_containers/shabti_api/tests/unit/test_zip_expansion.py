"""Telling an archive from a document that happens to be a zip, and expanding one.

No Tika, no OpenSearch and no pool: detection reads a central directory and expansion writes files,
so both are testable against the real asset with nothing but a temporary `SHABTI_FILES_DIR`.
"""

import os
import shutil
import zipfile
from io import BytesIO

import pytest

from ...src.app.functionality.insert_uploaded_files import (
    archive_member_names,
    expand_zip,
    is_plain_archive,
)
from ...src.app.functionality.content_hash import binary_hasher
from ...src.app.functionality.save_uploads import file_path

assets = os.path.join(os.path.dirname(__file__), "..", "assets")
archive_name = "test_docs.zip"
members = {"test_doc.txt", "test_doc_2.txt", "prompt_test.md"}


@pytest.fixture
def files_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SHABTI_FILES_DIR", str(tmp_path))
    return tmp_path


def staged_asset(files_dir, filename: str, name: str = "upload") -> str:
    """An asset copied in under a saved name, which is what an item id is."""
    shutil.copy(os.path.join(assets, filename), files_dir / name)
    return name


def staged_zip(files_dir, *names: str, name: str = "upload") -> str:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for member in names:
            archive.writestr(member, f"contents of {member}")
    (files_dir / name).write_bytes(buffer.getvalue())
    return name


def test_a_plain_zip_is_recognised_as_an_archive(files_dir):
    name = staged_asset(files_dir, archive_name)
    assert is_plain_archive(name)
    assert set(archive_member_names(name)) == members


@pytest.mark.parametrize(
    "marker",
    [
        "[Content_Types].xml",
        "mimetype",
        "META-INF/manifest.xml",
        "META-INF/MANIFEST.MF",
        "META-INF/container.xml",
    ],
)
def test_a_zip_based_document_format_is_not_an_archive(files_dir, marker):
    # a docx, odt, epub or jar is a zip too, and expanding one would replace the document with its
    # own internal XML parts
    name = staged_zip(files_dir, marker, "word/document.xml")
    assert not is_plain_archive(name)
    assert expand_zip(name, 100, 1000000) == []


def test_a_file_that_is_not_a_zip_is_not_an_archive(files_dir):
    assert not is_plain_archive(staged_asset(files_dir, "test_doc.txt"))


def test_expanding_an_archive_saves_every_member_as_its_own_upload(files_dir):
    name = staged_asset(files_dir, archive_name)
    expanded = expand_zip(name, 100, 1000000)
    assert {member.filename for member in expanded} == members
    assert {member.label for member in expanded} == members
    with zipfile.ZipFile(os.path.join(assets, archive_name)) as archive:
        for member in expanded:
            with open(file_path(member.item_id), "rb") as saved:
                assert saved.read() == archive.read(member.filename)
    # dropping the archive is the reader's job, once its members are queued
    assert os.path.exists(file_path(name))


def test_every_expanded_member_carries_a_hash_of_its_own_bytes(files_dir):
    # each member becomes a document of its own, and that hash is the id it is given, so a member
    # is deduplicated against the collection exactly as a directly uploaded file would be
    name = staged_asset(files_dir, archive_name)
    expanded = expand_zip(name, 100, 1000000)
    with zipfile.ZipFile(os.path.join(assets, archive_name)) as archive:
        for member in expanded:
            hasher = binary_hasher()
            hasher.update(archive.read(member.filename))
            assert member.binary_hash == hasher.hexdigest()
    assert len({member.binary_hash for member in expanded}) == len(expanded)


def test_expanding_basenames_member_paths(files_dir):
    # the label is what a client displays, and a member's path is not one the client ever sent
    name = staged_zip(files_dir, "docs/notes/a.txt")
    expanded = expand_zip(name, 100, 1000000)
    assert [member.filename for member in expanded] == ["a.txt"]
    assert not os.path.isdir(files_dir / "docs")


def test_an_archive_over_the_member_cap_is_refused(files_dir):
    name = staged_asset(files_dir, archive_name)
    with pytest.raises(ValueError):
        expand_zip(name, 1, 1000000)
    # nothing half expanded left behind: the caller throws the list away, so this is the only
    # chance anything has to delete the members that were already written
    assert os.listdir(files_dir) == [name]


def test_an_archive_over_the_byte_cap_leaves_nothing_behind(files_dir):
    name = staged_asset(files_dir, archive_name)
    # smaller than the archive's contents but larger than its first member
    with pytest.raises(Exception):
        expand_zip(name, 100, 50)
    assert os.listdir(files_dir) == [name]
