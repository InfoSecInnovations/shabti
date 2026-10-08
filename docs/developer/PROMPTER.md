# Prompter

A prompt is shaped by three kinds of `.shabti` file (ConfigObj) in
[docker_containers/shabti_api/prompter_config](../../docker_containers/shabti_api/prompter_config):

| Directory | Keys | Purpose |
|---|---|---|
| `tasks` | `greeting` (required), `prompt` (optional) | What to do with the user input. The greeting is the chat placeholder. |
| `personas` | `prompt` (required) | Who answers: tone, voice. |
| `enhancers` | `prompt` (required) | Extra instructions added after the task. Several can be picked. |

A task without a `greeting` breaks `GET /tasks`, and with it the whole prompter UI.

## How a prompt is built

`run_prompt` retrieves the most relevant chunks from the collection and streams them back as
sources before anything else. It then asks the loaded chat model, through chatlas, with:

- **system prompt**: persona, task, enhancers, in that order
- **user turn**: the retrieved context, the user input, then the source file if one was attached

## Tasks without a prompt

A task with no `prompt` key is a **search**: it returns the sources relevant to the query and
nothing else. `search` is the built-in one. Search means "find me the relevant documents", not
"answer from them", so the missing `prompt` is deliberate, not an oversight.

For such a task:

- the chat model is never called, and doesn't need to be loaded. The validator only requires one
  for tasks that have a prompt. The embeddings model is still needed for retrieval.
- the persona, enhancers and source file are ignored, as there is no response for them to shape.
  The web UI hides the Persona and Enhancers selectors while one is selected.
- the audit log entry records the sources, input and task, with no `response` key.

Any change to prompting (conversation history, tools, new chunk types, a new client) has to keep
this path working. It is covered by:

- `tests/unit/test_run_prompt.py`: the `test_a_search_*` tests
- `tests/unit/test_prompt_info_validator.py`: `PROMPTLESS` and the chat model tests
- `tests/security_disabled/test_prompting_api.py`: `test_search_without_a_loaded_chat_model`
