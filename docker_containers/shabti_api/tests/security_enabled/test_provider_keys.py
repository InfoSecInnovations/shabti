"""Users' own provider keys, stored in the real Keycloak.

`stub-byok` is the test llama.cpp again, configured to take only users' own keys, see
testing/stack.ts. llama.cpp ignores the key, so what's tested is whether Shabti offers and uses the
provider for a user who has added one.
"""

import json
import pytest
from shabti_keycloak import get_keycloak_client

USER = "testshared"
PROVIDER = "stub-byok"
KEY = "sk-test-" + "x" * 36 + "1234"


def auth(user=USER):
    token = get_keycloak_client().token(user, "test")
    return {"Authorization": f"Bearer {token['access_token']}"}


def chunks(response):
    return [json.loads(line) for line in response.text.splitlines() if line]


@pytest.fixture
def headers(shabti_client):
    headers = auth()
    shabti_client.delete(f"/providers/{PROVIDER}/credential", headers=headers)
    yield headers
    shabti_client.delete(f"/providers/{PROVIDER}/credential", headers=headers)


def chat_models(shabti_client, headers):
    response = shabti_client.get(
        "/providers/models", params={"category": "chat"}, headers=headers
    )
    assert response.status_code == 200
    return [x["id"] for x in response.json()]


def prompt(shabti_client, headers, collection_id, model):
    return shabti_client.post(
        "/prompt",
        json={
            "collection_id": collection_id,
            "task": "question",
            "user_input": "What does the word prompting mean?",
            "model": model,
        },
        headers=headers,
    )


async def test_without_a_key_the_provider_is_left_out(
    shabti_client, headers, loaded_chat_model
):
    assert shabti_client.get("/providers/credentials", headers=headers).json() == {}
    models = chat_models(shabti_client, headers)
    assert f"stub/{loaded_chat_model}" in models
    assert not any(x.startswith(f"{PROVIDER}/") for x in models)


@pytest.mark.parametrize(
    "shabti_collection_id",
    [{"username": "testadmin", "location": "shared"}],
    indirect=True,
)
async def test_a_prompt_without_a_key_asks_for_one(
    shabti_client, headers, shabti_collection_id, loaded_chat_model
):
    response = prompt(
        shabti_client, headers, shabti_collection_id, f"{PROVIDER}/{loaded_chat_model}"
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == "ProviderKeyMissingError"
    assert response.json()["message"] == "Add your API key for Stub BYOK"


@pytest.mark.parametrize(
    "shabti_collection_id",
    [{"username": "testadmin", "location": "shared"}],
    indirect=True,
)
async def test_a_key_is_added_used_and_removed(
    shabti_client,
    headers,
    shabti_collection_id,
    shabti_prompt_document_id,
    loaded_chat_model,
):
    model = f"{PROVIDER}/{loaded_chat_model}"
    response = shabti_client.put(
        f"/providers/{PROVIDER}/credential", json={"key": KEY}, headers=headers
    )
    assert response.status_code == 200
    # only ever the last four characters
    assert response.json() == {PROVIDER: "1234"}
    assert shabti_client.get("/providers/credentials", headers=headers).json() == {
        PROVIDER: "1234"
    }
    assert model in chat_models(shabti_client, headers)

    response = prompt(shabti_client, headers, shabti_collection_id, model)
    assert response.status_code == 200
    received = chunks(response)
    assert any(x.get("response") for x in received)
    assert not any(x.get("error") for x in received)

    response = shabti_client.delete(
        f"/providers/{PROVIDER}/credential", headers=headers
    )
    assert response.status_code == 200
    assert response.json() == {}
    assert model not in chat_models(shabti_client, headers)


async def test_a_long_key_round_trips_through_keycloak(shabti_client, headers):
    # sealed, it's well past the 255 characters Keycloak would hold without a long value column
    key = "k" * 996 + "5678"
    response = shabti_client.put(
        f"/providers/{PROVIDER}/credential", json={"key": key}, headers=headers
    )
    assert response.status_code == 200
    assert shabti_client.get("/providers/credentials", headers=headers).json() == {
        PROVIDER: "5678"
    }


async def test_a_key_is_the_users_own(shabti_client, headers, loaded_chat_model):
    shabti_client.put(
        f"/providers/{PROVIDER}/credential", json={"key": KEY}, headers=headers
    )
    other = auth("testadmin")
    assert shabti_client.get("/providers/credentials", headers=other).json() == {}
    assert f"{PROVIDER}/{loaded_chat_model}" not in chat_models(shabti_client, other)


async def test_storing_a_key_keeps_the_chat_model_selection(
    shabti_client, headers, loaded_chat_model
):
    selected = f"stub/{loaded_chat_model}"
    response = shabti_client.put(
        "/models/chat/selection", json={"model_name": selected}, headers=headers
    )
    assert response.status_code == 200
    shabti_client.put(
        f"/providers/{PROVIDER}/credential", json={"key": KEY}, headers=headers
    )
    response = shabti_client.get("/models/chat/selection", headers=headers)
    assert response.json()["model_name"] == selected


async def test_a_provider_taking_only_the_shared_key_refuses_a_users_key(
    shabti_client, headers
):
    response = shabti_client.put(
        "/providers/stub/credential", json={"key": KEY}, headers=headers
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == "UserKeysUnavailableError"
    assert shabti_client.get("/providers/credentials", headers=headers).json() == {}


async def test_the_keys_need_a_token(shabti_client):
    assert shabti_client.get("/providers/credentials").status_code in (401, 403)
