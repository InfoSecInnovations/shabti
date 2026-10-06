from shiny import ui, Inputs, Outputs, Session, module, reactive, render, req
from .collection_selector_ui import collection_selector_ui
from shabti_api_client import BaseShabtiClient
from .collections_data import CollectionsData
import asyncio
from shabti_types import TaskInfo, PromptConfigInfo, CollectionInfo
from .doc_page_link import page_link
from .source_text import source_text
from typing import TypeVar
from .load_models import load_models

REFERENCE_LIMIT = 5

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
    current_file_id = reactive.value(0)
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
            ),
            ui.layout_columns(*selectors),
            ui.layout_columns(
                ui.input_select(
                    id="task_select",
                    label="Task",
                    choices=task_list,
                    selected=selected_task,
                ),
                ui.input_select(
                    id="persona_select",
                    label="Persona",
                    choices=["None", *personas.get().keys()],
                ),
                ui.input_selectize(
                    id="enhancers_select",
                    label="Enhancers",
                    choices=list(enhancers.get()),
                    multiple=True,
                ),
            ),
            ui.output_ui("file_input"),
        )

    @reactive.effect
    @reactive.event(input.task_select)
    def update_chat_placeholder():
        tasks_dict = tasks.get()
        selected_task = input.task_select()
        task_list = list(tasks_dict)
        if selected_task in task_list:
            chat.update_user_input(placeholder=tasks_dict[selected_task].greeting)

    async def stream_response(
        collection_id: str,
        user_input: str,
        task: str,
        persona: str | None,
        selected_enhancers: list[str] | None,
        file_path: str | None,
    ):
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
            elif x.source:
                yield f"{page_link(collection_id, x.source)}\n\n{source_text(x.source.text)}\n\n"

    @chat.on_user_submit
    async def on_chat_submit(user_input: str):
        collection_id = selected_collection.get()
        task = input.task_select()
        persona = input.persona_select()
        selected_enhancers = input.enhancers_select()
        input_files = input[f"prompt_file_{current_file_id.get()}"]()
        file_path = None
        if input_files and len(input_files):
            file_path = input_files[0]["datapath"]
        await chat.append_message_stream(
            stream_response(
                collection_id,
                user_input,
                task,
                persona,
                selected_enhancers,
                file_path,
            )
        )

    # this will trigger after the chat message has been submitted
    @reactive.effect
    @reactive.event(chat.messages, ignore_none=False, ignore_init=True)
    def on_message():
        # this will clear the file input
        current_file_id.set(current_file_id.get() + 1)

    @render.ui
    @reactive.event(current_file_id, ignore_none=False, ignore_init=False)
    def file_input():
        return ui.input_file(
            id=f"prompt_file_{current_file_id.get()}", label="Source File (optional)"
        )

    @reactive.effect
    def update_config():
        req(api_status.get())
        load_prompter_config()
