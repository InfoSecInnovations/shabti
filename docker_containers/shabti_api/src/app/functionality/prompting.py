import httpx
import openai
from chatlas import ChatOpenAICompletions
from .providers.config import LOCAL
from .providers.errors import provider_error, upstream_detail
from .providers.reasoning import reasoning_request
from .providers.registry import ChatModel

# a remote API that stops answering has failed, however long the answer it was writing
REMOTE_TIMEOUT = httpx.Timeout(10, read=300)


def system_prompt(task_prompt, persona_prompt=None, enhancer_prompts=None):
    prompt = task_prompt

    if persona_prompt:
        prompt = persona_prompt + "\n\n" + prompt

    if enhancer_prompts:
        for enhancer_prompt in enhancer_prompts:
            prompt = prompt + "\n\n" + enhancer_prompt

    return prompt


def user_turn(context, user_input, source_file_contents=None):
    prompt = "Context: " + context + "\n\nUser input: " + user_input

    if source_file_contents:
        prompt = prompt + "\n\nSource file: " + source_file_contents

    return prompt


async def stream_response(
    model: ChatModel,
    api_key: str | None,
    user: str | None,
    context,
    task_prompt,
    user_input,
    persona_prompt=None,
    enhancer_prompts=None,
    source_file_contents=None,
    reasoning=None,
):
    provider = model.provider
    chat = ChatOpenAICompletions(
        base_url=provider.base_url,
        model=model.model_id,
        # llama.cpp and other keyless servers don't check it, but the OpenAI SDK refuses to start
        # without one
        api_key=api_key or "none",
        system_prompt=system_prompt(task_prompt, persona_prompt, enhancer_prompts),
        kwargs={
            # a long answer on a slow machine is still an answer
            "timeout": None if provider.id == LOCAL else REMOTE_TIMEOUT,
            "default_headers": dict(provider.headers),
        },
    )
    # in the body rather than as `user`, since not every provider calls the field that
    body = {provider.user_id_field: user} if user else {}
    # None leaves the model thinking as it does by default
    if reasoning is not None:
        body |= reasoning_request(provider, reasoning)
    request = {"extra_body": body} if body else None
    try:
        async for x in await chat.stream_async(
            user_turn(context, user_input, source_file_contents),
            content="all",
            kwargs=request,
        ):
            yield x
    except openai.APIError as e:
        responded = isinstance(e, openai.APIStatusError)
        raise provider_error(
            e.status_code if responded else None,
            model.id,
            purpose="chat",
            # the parsed error rather than e.message, which carries the whole response
            detail=upstream_detail(e.body, api_key) if responded else None,
        ) from e
