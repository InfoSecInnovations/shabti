from shiny import module, reactive, ui, render, Inputs, Outputs, Session
from ..common.ingester import ingester_ui, ingester_server
from ..common.collections_data import CollectionsData
from shabti_api_client import ShabtiClient
from .collection_create import collection_create_ui, collection_create_server
from ..common.collection_selector_ui import collection_selector_ui
from .collection_selector_server import collection_selector_server
from ..common.document_list import document_list_ui, document_list_server
from shabti_types import CollectionInfo


@module.server
def collection_management_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: ShabtiClient,
    selected_collection: reactive.Value,
    collections: reactive.Value[CollectionsData[CollectionInfo]],
    api_status: reactive.Value,
    opensearch_status: reactive.Value,
    llm_status: reactive.Value,
    tika_status: reactive.Value,
):
    collection_create_server(
        "collection_create", client, selected_collection, collections
    )
    collection_selector_server("collection_select", selected_collection, collections)
    ingestion_done_trigger = ingester_server(
        "ingester", client, selected_collection, collections, llm_status, tika_status
    )

    @render.ui
    def collection_management_content():
        if not api_status.get() or not opensearch_status.get():
            return ui.markdown("Requirements are not online, see sidebar!")
        elements = [collection_create_ui("collection_create")]
        if selected_collection.get():
            elements.append(collection_selector_ui("collection_select"))
        elements.append(ui.output_ui("collection_view"))
        return ui.TagList(*elements)

    @render.ui
    def collection_view():
        if selected_collection.get():
            return ui.TagList(
                [
                    ui.markdown(
                        f"### Selected collection: {collections.get().collections[selected_collection.get()].collection_name}"
                    ),
                    ui.accordion(
                        ingester_ui("ingester"),
                        ui.accordion_panel(
                            ui.markdown("#### Manage Documents"),
                            ui.output_ui("collection_documents"),
                            value="manage_documents",
                        ),
                        id="collection_management_accordion",
                        class_="mb-3",
                    ),
                    ui.input_task_button(id="delete", label="Delete Collection"),
                ]
            )
        if collections.get().loading:
            return ui.markdown("Loading collections...")
        return ui.markdown("Please create a collection first!")

    @render.ui
    def collection_documents():
        return document_list_ui("document_list")

    @ui.bind_task_button(button_id="delete")
    @reactive.extended_task
    async def delete(collection_id):
        await client.delete_collection(collection_id)
        return await client.get_collections()

    @reactive.effect
    def delete_effect():
        new_collections = delete.result()
        collections.set(
            CollectionsData(
                collections={
                    collection.collection_id: collection
                    for collection in new_collections
                },
                loading=False,
            )
        )
        if len(new_collections):
            selected_collection.set(new_collections[0].collection_id)
        else:
            selected_collection.set(None)

    @reactive.effect
    @reactive.event(input.delete, ignore_init=True)
    def on_delete():
        delete(selected_collection.get())

    @reactive.effect
    @reactive.event(ingestion_done_trigger)
    def on_ingestion_done():
        ui.update_accordion_panel(
            "collection_management_accordion", "manage_documents", show=True
        )

    document_list_server(
        "document_list", client, selected_collection, None, ingestion_done_trigger
    )
