"""Users' own provider keys. `stub-byok` is the test llama.cpp taking only users' own keys, see
testing/stack.ts."""

import pytest
from shabti_api_client import ShabtiRequestError

PROVIDER = "stub-byok"


@pytest.mark.parametrize("shabti_user_client", ["testshared"], indirect=True)
async def test_a_key_is_added_listed_and_removed(shabti_user_client, loaded_chat_model):
    model = f"{PROVIDER}/{loaded_chat_model}"
    try:
        assert await shabti_user_client.set_api_key(PROVIDER, "sk-test-1234") == {
            PROVIDER: "1234"
        }
        assert await shabti_user_client.get_api_keys() == {PROVIDER: "1234"}
        models = await shabti_user_client.get_provider_models("chat")
        assert model in [x.id for x in models]
    finally:
        assert await shabti_user_client.delete_api_key(PROVIDER) == {}
    models = await shabti_user_client.get_provider_models("chat")
    assert model not in [x.id for x in models]


@pytest.mark.parametrize("shabti_user_client", ["testshared"], indirect=True)
async def test_a_provider_taking_only_the_shared_key_refuses_a_users_key(
    shabti_user_client,
):
    with pytest.raises(ShabtiRequestError) as raised:
        await shabti_user_client.set_api_key("stub", "sk-test-1234")
    assert raised.value.status_code == 400
    assert await shabti_user_client.get_api_keys() == {}
