import aiofiles
from chatlas.types import ContentThinkingDelta
from shabti_types import PromptInfo, PromptChunk, PromptSource, DocumentInfo, PageInfo
from .prompting import stream_response
from .opensearch_prompting import get_context_from_opensearch
from .opensearch import get_temp_file
from .load_prompter_config import load_prompter_config
from ..shabti_logging import log_user_action, logging_enabled
from .document_collections import get_collection_info
from .models import get_loaded_chat_model
from .settings import setting


async def run_prompt(token: None | str, prompt_info: PromptInfo):
    tasks = load_prompter_config("tasks")
    # a task without a prompt (`search`) only finds the sources, and never reaches the chat model:
    # see docs/developer/PROMPTER.md
    task_prompt = tasks[prompt_info.task].get("prompt")

    persona_prompt = None
    enhancer_prompts = None
    source_file_contents = None

    # the persona, enhancers and source file only shape what the chat model writes
    if task_prompt:
        if prompt_info.persona:
            personas = load_prompter_config("personas")
            persona_prompt = personas[prompt_info.persona]["prompt"]

        if prompt_info.enhancers:
            enhancers = load_prompter_config("enhancers")
            enhancer_prompts = []
            for enhancer in prompt_info.enhancers:
                enhancer_prompts.append(enhancers[enhancer]["prompt"])

        if prompt_info.file_id:
            file_path = await get_temp_file(prompt_info.file_id)
            async with aiofiles.open(file_path) as f:
                source_file_contents = await f.read()

    context = await get_context_from_opensearch(
        prompt_info.collection_id,
        setting("SHABTI_PROMPT_REFERENCE_LIMIT"),
        prompt_info.user_input,
    )

    if not len(context["sources"]):
        yield PromptChunk(
            response="No sources were found matching your query. Please refine your request to closer match the data in the database or ingest more data."
        )
        # TODO: log the no sources response
        return

    async def log_prompt(response: str | None):
        prompt = {
            "sources": context["sources"],
            "input": prompt_info.user_input,
            "task": prompt_info.task,
        }
        # a search has no response, as nothing was generated
        if response is not None:
            prompt["response"] = response
        if prompt_info.persona and task_prompt:
            prompt["persona"] = prompt_info.persona
        if prompt_info.enhancers and task_prompt:
            prompt["enhancers"] = prompt_info.enhancers
        if prompt_info.file_id and task_prompt:
            prompt["input_file"] = {
                "file_id": prompt_info.file_id,
                "contents": source_file_contents,
            }
        await log_user_action(
            token,
            "QUERY",
            f"User ran a prompt on collection with ID {prompt_info.collection_id}",
            collection=(
                await get_collection_info(prompt_info.collection_id)
            ).model_dump(),
            prompt=prompt,
        )

    for source in context["sources"]:
        yield PromptChunk(
            source=PromptSource(
                document_metadata=DocumentInfo(**source["doc_metadata"]),
                page_metadata=PageInfo(**source["page_metadata"]),
                text=source["text"],
            )
        )

    if not task_prompt:
        if logging_enabled():
            await log_prompt(None)
        return

    response = ""
    model_name = await get_loaded_chat_model()
    async for x in stream_response(
        model_name=model_name,
        context=context["context"],
        task_prompt=task_prompt,
        user_input=prompt_info.user_input,
        persona_prompt=persona_prompt,
        enhancer_prompts=enhancer_prompts,
        source_file_contents=source_file_contents,
    ):
        if isinstance(x, ContentThinkingDelta):
            if x.thinking:
                yield PromptChunk(thinking=x.thinking)
        # nothing else is expected: no tools are registered, so the stream is only ever text
        elif isinstance(x, str) and x:
            yield PromptChunk(response=x)
            if logging_enabled():
                response += x

    if logging_enabled():
        await log_prompt(response)
