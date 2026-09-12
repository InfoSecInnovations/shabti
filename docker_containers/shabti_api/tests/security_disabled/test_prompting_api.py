from ...src.app.functionality.ingesting import insert_document
import os
from ...src.app.functionality.loading import load_file
from ...src.app.functionality.models import get_models, load_model, unload_models
from uuid import uuid4

filename = "prompt_test.md"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


# the main purpose of this test is just to ensure prompting can run without errors as the output isn't expected to be deterministic
async def test_prompt(shabti_client, shabti_collection_id, loaded_chat_model):
    unique_filename = uuid4().hex
    with open(file_path, "rb") as f:
        doc = load_file(f, filename)
    async for _ in insert_document(None, shabti_collection_id, doc, unique_filename):
        pass
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": shabti_collection_id,
            "task": "question",
            "user_input": "What does the word prompting mean?",
        },
    )
    assert response.status_code == 200


# the prompt runs on whichever chat model is loaded, so there is nothing to run if none is
async def test_prompt_without_a_loaded_chat_model(
    shabti_client, shabti_collection_id, loaded_chat_model
):
    chat_models = (await get_models(tags=["chat"]))["data"]
    async for _ in unload_models([x["id"] for x in chat_models]):
        pass
    try:
        response = shabti_client.post(
            "/prompt",
            json={
                "collection_id": shabti_collection_id,
                "task": "question",
                "user_input": "What does the word prompting mean?",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "No chat model is loaded"
    finally:
        async for _ in load_model(loaded_chat_model):
            pass


# the task, persona and enhancer checks are unit tested against the validator itself; these two are
# here to show it is actually wired to the route, and that a bad request is refused with a status
# rather than part way through a stream that has already started
async def test_prompt_with_an_unknown_task(
    shabti_client, shabti_collection_id, loaded_chat_model
):
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": shabti_collection_id,
            "task": "not-a-task",
            "user_input": "What does the word prompting mean?",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Requested task not found"


async def test_prompt_with_an_unknown_persona(
    shabti_client, shabti_collection_id, loaded_chat_model
):
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": shabti_collection_id,
            "task": "question",
            "persona": "not-a-persona",
            "user_input": "What does the word prompting mean?",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Requested persona not found"
