import aiofiles
from shabti_types import PromptInfo, PromptChunk, PromptSource, DocumentInfo, PageInfo
from .prompting import stream_response
from .opensearch_prompting import get_context_from_opensearch
from .opensearch import get_temp_file
from .load_prompter_config import load_prompter_config
import json
from ..shabti_logging import log_user_action, logging_enabled
from .document_collections import get_collection_info
from .models import get_loaded_chat_model


async def run_prompt(token: None | str, prompt_info: PromptInfo):
    tasks = load_prompter_config("tasks")
    task_prompt = tasks[prompt_info.task]["prompt"]

    if prompt_info.persona:
        personas = load_prompter_config("personas")
        persona_prompt = personas[prompt_info.persona]["prompt"]

    else:
        persona_prompt = None

    if prompt_info.enhancers:
        enhancers = load_prompter_config("enhancers")
        enhancer_prompts = []
        for enhancer in prompt_info.enhancers:
            enhancer_prompts.append(enhancers[enhancer]["prompt"])
    else:
        enhancer_prompts = None

    if prompt_info.file_id:
        file_path = await get_temp_file(prompt_info.file_id)
        async with aiofiles.open(file_path) as f:
            source_file_contents = await f.read()
    else:
        source_file_contents = None

    context = await get_context_from_opensearch(
        prompt_info.collection_id, 5, prompt_info.user_input
    )

    if not len(context["sources"]):
        yield PromptChunk(
            response="No sources were found matching your query. Please refine your request to closer match the data in the database or ingest more data."
        )
        # TODO: log the no sources response
        return

    response = ""
    for source in context["sources"]:
        yield PromptChunk(
            source=PromptSource(
                document_metadata=DocumentInfo(**source["doc_metadata"]),
                page_metadata=PageInfo(**source["page_metadata"]),
            )
        )
    model_name = await get_loaded_chat_model()
    async for x in stream_response(
        model_name=model_name,
        context=context["context"],
        task_prompt=task_prompt,
        user_input=prompt_info.user_input,
        persona_prompt=persona_prompt,
        source_file_contents=source_file_contents,
    ):
        try:
            obj = json.loads(x)
            if "choices" in obj and "content" in obj["choices"][0]["delta"]:
                yield PromptChunk(response=obj["choices"][0]["delta"]["content"])
                if logging_enabled():
                    response += obj["choices"][0]["delta"]["content"]
        except json.decoder.JSONDecodeError as e:
            if x.startswith("[DONE]"):
                return
            raise e

    if logging_enabled():
        prompt = {
            "response": response,
            "sources": context["sources"],
            "input": prompt_info.user_input,
            "task": prompt_info.task,
        }
        if prompt_info.persona:
            prompt["persona"] = prompt_info.persona
        if prompt_info.enhancers:
            prompt["enhancers"] = prompt_info.enhancers
        if prompt_info.file_id:
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
