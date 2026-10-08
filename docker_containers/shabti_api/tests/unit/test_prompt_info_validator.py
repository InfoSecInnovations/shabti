"""What `POST /prompt` accepts, checked before anything starts streaming.

The validator exists because a prompt's response is a stream: once the first chunk is out there is
no status code left to reject the request with. So everything it can know up front it has to check
up front, and that is what this file pins.

Only the chat model lookup is stubbed. The prompter config is read for real, from the directory the
app itself reads, so adding a task or renaming a persona is covered here without touching the tests
- and a config the app could not actually serve fails here rather than in front of a user.
"""

import pytest
from fastapi import HTTPException
from shabti_types import ModelNotFoundError, PromptInfo

from ...src.app.dependencies import prompt_info_validator as validator_module
from ...src.app.dependencies.prompt_info_validator import PromptInfoValidator
from ...src.app.functionality.load_prompter_config import load_prompter_config

TASKS = sorted(load_prompter_config("tasks"))
PERSONAS = sorted(load_prompter_config("personas"))
ENHANCERS = sorted(load_prompter_config("enhancers"))
# the tasks that only find the sources, never generating a response: see docs/developer/PROMPTER.md
PROMPTLESS = sorted(
    task
    for task, config in load_prompter_config("tasks").items()
    if not config.get("prompt")
)


def prompt_info(**overrides):
    return PromptInfo(
        **{
            "collection_id": "a-collection",
            "task": TASKS[0],
            "user_input": "what is in this collection?",
            **overrides,
        }
    )


@pytest.fixture
def loaded_chat_model(monkeypatch):
    """A chat model is loaded, which is the case every other test here assumes."""

    async def get_loaded_chat_model():
        return "chat-1"

    monkeypatch.setattr(
        validator_module, "get_loaded_chat_model", get_loaded_chat_model
    )


@pytest.fixture
def no_chat_model(monkeypatch):
    async def get_loaded_chat_model():
        raise ModelNotFoundError(message="No chat model is loaded")

    monkeypatch.setattr(
        validator_module, "get_loaded_chat_model", get_loaded_chat_model
    )


@pytest.fixture
def config_reads(monkeypatch):
    """Which config directories were read, in order."""
    reads = []

    def load(directory):
        reads.append(directory)
        return load_prompter_config(directory)

    monkeypatch.setattr(validator_module, "load_prompter_config", load)
    return reads


def test_the_config_directories_are_not_empty():
    # every assertion below is parametrized over these, so an empty one would quietly test nothing
    assert TASKS and PERSONAS and ENHANCERS


def test_there_is_a_task_without_a_prompt():
    # `search` is one by design, and the tests below parametrized over these would otherwise
    # quietly stop covering the path that never reaches the chat model
    assert PROMPTLESS


@pytest.mark.parametrize("task", TASKS)
async def test_every_task_is_accepted(loaded_chat_model, task):
    # including the tasks that carry no `prompt` of their own: `search` returns the matching
    # sources without ever generating a response, so having nothing to prompt with is deliberate
    await PromptInfoValidator()(prompt_info(task=task))


@pytest.mark.parametrize("persona", PERSONAS)
async def test_every_persona_is_accepted(loaded_chat_model, persona):
    await PromptInfoValidator()(prompt_info(persona=persona))


@pytest.mark.parametrize("enhancer", ENHANCERS)
async def test_every_enhancer_is_accepted(loaded_chat_model, enhancer):
    await PromptInfoValidator()(prompt_info(enhancers=[enhancer]))


async def test_every_enhancer_at_once_is_accepted(loaded_chat_model):
    await PromptInfoValidator()(prompt_info(enhancers=ENHANCERS))


async def test_a_task_a_persona_and_enhancers_together_are_accepted(loaded_chat_model):
    await PromptInfoValidator()(
        prompt_info(task=TASKS[-1], persona=PERSONAS[0], enhancers=ENHANCERS)
    )


async def test_an_unknown_task_is_rejected(loaded_chat_model):
    with pytest.raises(HTTPException) as raised:
        await PromptInfoValidator()(prompt_info(task="not-a-task"))
    assert raised.value.status_code == 400
    assert raised.value.detail == "Requested task not found"


async def test_an_unknown_persona_is_rejected(loaded_chat_model):
    with pytest.raises(HTTPException) as raised:
        await PromptInfoValidator()(prompt_info(persona="not-a-persona"))
    assert raised.value.status_code == 400
    assert raised.value.detail == "Requested persona not found"


async def test_an_unknown_enhancer_is_rejected(loaded_chat_model):
    with pytest.raises(HTTPException) as raised:
        await PromptInfoValidator()(prompt_info(enhancers=["not-an-enhancer"]))
    assert raised.value.status_code == 400
    assert raised.value.detail == "Requested enhancer not found"


async def test_one_bad_enhancer_among_good_ones_is_rejected(loaded_chat_model):
    # every element is checked, not just the first: a request is only as valid as its worst part
    with pytest.raises(HTTPException) as raised:
        await PromptInfoValidator()(
            prompt_info(enhancers=[*ENHANCERS, "not-an-enhancer"])
        )
    assert raised.value.detail == "Requested enhancer not found"


async def test_a_plain_prompt_reads_only_the_tasks(loaded_chat_model, config_reads):
    # each read re-lists and re-parses a whole directory, so skipping the lookups for what wasn't
    # asked for is worth keeping
    await PromptInfoValidator()(prompt_info())
    assert config_reads == ["tasks"]


@pytest.mark.parametrize("enhancers", [None, []])
async def test_no_enhancers_reads_no_enhancers(
    loaded_chat_model, config_reads, enhancers
):
    await PromptInfoValidator()(prompt_info(enhancers=enhancers))
    assert config_reads == ["tasks"]


async def test_a_full_prompt_reads_each_directory_once(loaded_chat_model, config_reads):
    await PromptInfoValidator()(prompt_info(persona=PERSONAS[0], enhancers=ENHANCERS))
    assert config_reads == ["tasks", "personas", "enhancers"]


async def test_no_loaded_chat_model_is_rejected(no_chat_model):
    with pytest.raises(HTTPException) as raised:
        await PromptInfoValidator()(prompt_info())
    assert raised.value.status_code == 400
    assert raised.value.detail == "No chat model is loaded"


async def test_an_unknown_task_is_reported_before_the_missing_model(no_chat_model):
    # whether a chat model is needed at all depends on the task, so the task is checked first
    with pytest.raises(HTTPException) as raised:
        await PromptInfoValidator()(prompt_info(task="not-a-task"))
    assert raised.value.detail == "Requested task not found"


@pytest.mark.parametrize("task", PROMPTLESS)
async def test_a_task_without_a_prompt_needs_no_chat_model(no_chat_model, task):
    # it only finds the sources, which takes the embeddings model and nothing else
    await PromptInfoValidator()(prompt_info(task=task))


@pytest.mark.parametrize("task", sorted(set(TASKS) - set(PROMPTLESS)))
async def test_a_task_with_a_prompt_needs_a_chat_model(no_chat_model, task):
    with pytest.raises(HTTPException) as raised:
        await PromptInfoValidator()(prompt_info(task=task))
    assert raised.value.detail == "No chat model is loaded"


@pytest.mark.parametrize("task", TASKS)
def test_every_task_has_a_greeting(task):
    # `TaskInfo` requires one, so a task without it takes out `GET /tasks` and with it the whole
    # prompter UI, not just that task. `prompt` is deliberately not required: see `search`
    assert load_prompter_config("tasks")[task].get("greeting")


@pytest.mark.parametrize("persona", PERSONAS)
def test_every_persona_has_a_prompt(persona):
    # unlike a task, a persona is nothing but its prompt, and `run_prompt` indexes it directly
    assert load_prompter_config("personas")[persona].get("prompt")


@pytest.mark.parametrize("enhancer", ENHANCERS)
def test_every_enhancer_has_a_prompt(enhancer):
    assert load_prompter_config("enhancers")[enhancer].get("prompt")
