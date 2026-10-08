import os
from chatlas import ChatOpenAICompletions


def host():
    return os.getenv("LLM_HOST") or "localhost"


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
    model_name,
    context,
    task_prompt,
    user_input,
    persona_prompt=None,
    enhancer_prompts=None,
    source_file_contents=None,
):
    chat = ChatOpenAICompletions(
        base_url=f"http://{host()}:11434/v1",
        model=model_name,
        # llama.cpp doesn't check it, but the OpenAI SDK refuses to start without one
        api_key="none",
        system_prompt=system_prompt(task_prompt, persona_prompt, enhancer_prompts),
        # a long answer on a slow machine is still an answer
        kwargs={"timeout": None},
    )
    async for x in await chat.stream_async(
        user_turn(context, user_input, source_file_contents), content="all"
    ):
        yield x
