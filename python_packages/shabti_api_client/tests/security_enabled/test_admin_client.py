from .lib import get_admin_client
from shabti_api_client import ShabtiRequestError
import pytest


@pytest.mark.parametrize(
    "shabti_collection_id",
    [
        ({"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_can_read_collection(shabti_collection_id):
    admin_client = await get_admin_client()
    collections = await admin_client.get_collections()
    assert len(collections)
    assert next(
        (
            collection
            for collection in collections
            if collection.collection_id == shabti_collection_id
        ),
        None,
    )


@pytest.mark.parametrize(
    "shabti_collection_id",
    [
        ({"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_can_delete_collection(shabti_collection_id):
    admin_client = await get_admin_client()
    await admin_client.delete_collection(shabti_collection_id)
    collections = await admin_client.get_collections()
    assert not any(
        collection.collection_id == shabti_collection_id for collection in collections
    )


# each user keeps their own, so another user's choice doesn't change what this one prompts with
async def test_a_remote_chat_model_selection_is_kept(loaded_chat_model):
    admin_client = await get_admin_client()
    previous = await admin_client.get_chat_model_selection()
    try:
        await admin_client.set_chat_model_selection(f"stub/{loaded_chat_model}")
        assert (
            await admin_client.get_chat_model_selection() == f"stub/{loaded_chat_model}"
        )
    finally:
        await admin_client.set_chat_model_selection(previous)


async def test_an_unknown_chat_model_is_not_selected(loaded_chat_model):
    admin_client = await get_admin_client()
    with pytest.raises(ShabtiRequestError) as raised:
        await admin_client.set_chat_model_selection("stub/not-a-model")
    assert raised.value.status_code == 400
