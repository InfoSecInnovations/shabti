from shiny import ui, Inputs, Outputs, Session, module, reactive, render, req
from .collection_selector_ui import collection_selector_ui
from shabti_api_client import BaseShabtiClient
from .collections_data import CollectionsData
import asyncio
from shabti_types import TaskInfo, PromptConfigInfo, CollectionInfo
from .doc_page_link import page_link, source_label
from typing import TypeVar
from .load_models import load_models
from chatlas.types import (
    ContentThinkingDelta,
    ContentToolRequest,
    ContentToolResult,
    ToolInfo,
)
from shinychat.types import Attachment, ToolResultDisplay
import json
import os
import tempfile
import urllib.request
import uuid

# the API reads a source file as text, so only shinychat's text types are offered
TEXT_ATTACHMENT_TYPES = [
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "text/html",
    "application/xml",
    "text/yaml",
    "text/x-rst",
    "text/x-tex",
]

# retrieval isn't a tool the LLM calls, but each source is shown as if it was one, so each gets a
# row above the answer that expands to the text it was given
SOURCES_TOOL = ToolInfo(
    name="retrieve_sources",
    description="",
    parameters={},
    annotations={"title": "Source"},
)

TCollectionInfo = TypeVar("TCollectionInfo", bound=CollectionInfo)


@module.ui
def prompter_ui():
    return [ui.markdown("# Prompter"), ui.output_ui("prompter_ui")]


@module.server
def prompter_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: BaseShabtiClient,
    selected_collection: reactive.Value,
    collections: reactive.Value[CollectionsData[TCollectionInfo]],
    api_status: reactive.Value,
    opensearch_status: reactive.Value,
    llm_status: reactive.Value,
    collection_selector_server,
):
    llm_loaded = reactive.value(False)
    tasks: reactive.Value[dict[str, TaskInfo] | None] = reactive.value(None)
    personas: reactive.Value[dict[str, PromptConfigInfo] | None] = reactive.value(None)
    enhancers: reactive.Value[dict[str, PromptConfigInfo] | None] = reactive.value(None)
    chat_models: reactive.Value[list[dict] | None] = reactive.value(None)
    current_model: reactive.Value[str | None] = reactive.value(None)

    @reactive.extended_task
    async def load_prompter_config():
        return await asyncio.gather(
            client.get_tasks(), client.get_personas(), client.get_enhancers()
        )

    @reactive.effect
    def load_config_effect():
        tasks_result, personas_result, enhancers_result = load_prompter_config.result()
        tasks.set(tasks_result)
        personas.set(personas_result)
        enhancers.set(enhancers_result)

    collection_selector_server("collection_selector", selected_collection, collections)
    chat = ui.Chat(id="prompter_chat")

    @reactive.extended_task
    async def load_selected_model(model_name: str):
        await load_models(client, model_name)

    @reactive.effect
    def load_selected_model_effect():
        load_selected_model.result()
        llm_loaded.set(True)

    @reactive.extended_task
    async def init_models():
        models, selection = await asyncio.gather(
            client.get_models(tags=["chat"]), client.get_chat_model_selection()
        )
        return models["data"], selection

    @reactive.effect
    def init_models_effect():
        models_list, selected_id = init_models.result()
        chat_models.set(models_list)
        current_model.set(selected_id)

    @reactive.extended_task
    async def persist_model_selection(model_name: str):
        await client.set_chat_model_selection(model_name)

    @reactive.effect
    @reactive.event(input.model_select, ignore_none=True, ignore_init=True)
    def on_model_select():
        llm_loaded.set(False)
        current_model.set(input.model_select())
        persist_model_selection(input.model_select())

    @reactive.effect
    @reactive.event(current_model, ignore_none=True, ignore_init=True)
    def on_current_model_set():
        load_selected_model(current_model.get())

    @reactive.effect
    def init():
        if llm_status.get() and not chat_models.get():
            init_models()

    @render.ui
    def prompter_ui():
        loaded = (
            llm_loaded.get()
            and llm_status.get()
            and opensearch_status.get()
            and tasks.get()
        )
        if loaded:
            if not len(collections.get().collections):
                return ui.markdown(
                    "Please create a collection and ingest some documents into it first!"
                )
            return ui.output_ui("chat_area")
        if not api_status.get() or not llm_status.get() or not opensearch_status.get():
            return ui.markdown("Requirements are not online, see sidebar!")
        if not tasks.get():
            return ui.markdown("Loading prompter config, please wait...")
        return ui.markdown("Loading Language Model, please wait...")

    @render.ui
    def chat_area():
        tasks_dict = tasks.get()
        task_list = list(tasks_dict)
        selected_task = task_list[0] if "question" not in tasks_dict else "question"
        selectors = [collection_selector_ui("collection_selector")]
        # a task without a prompt only searches, so there's no response for these to shape:
        # see docs/developer/PROMPTER.md
        promptless = [name for name, task in tasks_dict.items() if not task.prompt]
        shapes_response = f"!{json.dumps(promptless)}.includes(input.task_select)"
        # we only display the model selector if more than one model is available
        if len(chat_models.get()) > 1:
            selectors.append(
                ui.input_select(
                    id="model_select",
                    label="Model",
                    choices=[m["id"] for m in chat_models.get()],
                    selected=current_model.get(),
                )
            )
        return ui.TagList(
            ui.chat_ui(
                id="prompter_chat",
                placeholder=tasks_dict[selected_task].greeting,
                show_history=False,
                enable_cancel=True,
                allow_attachments=TEXT_ATTACHMENT_TYPES,
                # one row per source rather than one for them all, as a search is nothing but these
                tool_grouping="none",
            ),
            ui.layout_columns(*selectors),
            ui.layout_columns(
                ui.input_select(
                    id="task_select",
                    label="Task",
                    choices=task_list,
                    selected=selected_task,
                ),
                ui.panel_conditional(
                    shapes_response,
                    ui.input_select(
                        id="persona_select",
                        label="Persona",
                        choices=["None", *personas.get().keys()],
                    ),
                ),
                ui.panel_conditional(
                    shapes_response,
                    ui.input_selectize(
                        id="enhancers_select",
                        label="Enhancers",
                        choices=list(enhancers.get()),
                        multiple=True,
                    ),
                ),
            ),
        )

    @reactive.effect
    @reactive.event(input.task_select)
    def update_chat_placeholder():
        tasks_dict = tasks.get()
        selected_task = input.task_select()
        task_list = list(tasks_dict)
        if selected_task in task_list:
            chat.update_user_input(placeholder=tasks_dict[selected_task].greeting)

    def source_result(collection_id: str, user_input: str, source):
        request = ContentToolRequest(
            id=uuid.uuid4().hex,
            name=SOURCES_TOOL.name,
            arguments={"query": user_input},
            tool=SOURCES_TOOL,
        )
        result = ContentToolResult(
            value=source.text,
            request=request,
            extra={
                "display": ToolResultDisplay(
                    label=source_label(source),
                    show_request=False,
                    html=ui.TagList(
                        ui.markdown(page_link(collection_id, source)),
                        ui.div(source.text, class_="shabti-source-text"),
                    ),
                )
            },
        )
        return request, result

    async def stream_response(
        collection_id: str,
        user_input: str,
        task: str,
        persona: str | None,
        selected_enhancers: list[str] | None,
        file_path: str | None,
    ):
        try:
            async for x in client.prompt(
                collection_id,
                user_input,
                task,
                None if not persona or persona == "None" else persona,
                selected_enhancers,
                file_path,
            ):
                if x.response:
                    yield x.response
                elif x.thinking:
                    yield ContentThinkingDelta(thinking=x.thinking)
                elif x.source:
                    for content in source_result(collection_id, user_input, x.source):
                        yield content
        finally:
            if file_path:
                os.remove(file_path)

    def attachments_file(attachments: list[Attachment]):
        """The attachments as one file, which is what a prompt takes"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(
                b"\n\n".join(
                    urllib.request.urlopen(a.data_url).read() for a in attachments
                )
            )
        return f.name

    @chat.on_user_submit
    async def on_chat_submit(user_input: str, attachments: list[Attachment]):
        await chat.append_message_stream(
            stream_response(
                selected_collection.get(),
                user_input,
                input.task_select(),
                input.persona_select(),
                input.enhancers_select(),
                attachments_file(attachments) if attachments else None,
            )
        )

    @reactive.effect
    @reactive.event(input.prompter_chat_cancel)
    def on_chat_cancel():
        chat.latest_message_stream.cancel()

    @reactive.effect
    def update_config():
        req(api_status.get())
        load_prompter_config()
