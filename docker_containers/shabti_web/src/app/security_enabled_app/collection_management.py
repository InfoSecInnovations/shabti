from shiny import module, reactive, ui, render, Inputs, Outputs, Session
from .collection_create import collection_create_ui, collection_create_server
from .collection_selector_server import collection_selector_server
from ..common.collection_selector_ui import collection_selector_ui
from ..common.ingester import ingester_ui, ingester_server
from .format_collection_name import format_collection_name
from ..common.collections_data import CollectionsData
from shabti_api_client import ShabtiAuthorizationClient
from shabti_types import AuthzCollectionInfo
from ..common.document_list import document_list_ui, document_list_server


@module.server
def collection_management_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: ShabtiAuthorizationClient,
    selected_collection: reactive.Value,
    collections: reactive.Value[CollectionsData[AuthzCollectionInfo]],
    api_status: reactive.Value,
    opensearch_status: reactive.Value,
    user_info: reactive.Value,
    permissions: reactive.Value[set],
    llm_status: reactive.Value,
    tika_status: reactive.Value,
):
    collection_create_server(
        "collection_create", client, selected_collection, collections, permissions
    )
    collection_selector_server(
        "collection_select", selected_collection, collections, user_info
    )
    ingestion_done_trigger = ingester_server(
        "ingester", client, selected_collection, collections, llm_status, tika_status
    )
    current_scopes = reactive.value(set())
    fetching_scopes = reactive.value(False)

    @render.ui
    def collection_management_content():
        if not api_status.get() or not opensearch_status.get():
            return ui.markdown("Requirements are not online, see sidebar!")
        perms = permissions.get()
        show_toggle = (
            "collection:shared:create" in perms and "collection:private:create" in perms
        )
        elements = [collection_create_ui("collection_create", show_toggle)]
        if selected_collection.get():
            elements.append(collection_selector_ui("collection_select"))
        elements.append(ui.output_ui("collection_view"))
        return ui.TagList(*elements)

    @render.ui
    def collection_view():
        if selected_collection.get():
            accordion_elements = []
            fetching = fetching_scopes.get()
            if fetching:
                accordion_elements.append(
                    ui.accordion_panel(
                        ui.markdown("#### Loading permissions..."),
                        value="ingest_documents",
                    )
                )
            elif "update" in current_scopes.get():
                accordion_elements.append(ingester_ui("ingester"))
            else:
                accordion_elements.append(
                    ui.accordion_panel(
                        ui.markdown(
                            "#### You don't have permission to ingest documents into this collection"
                        ),
                        value="ingest_documents",
                    )
                )
            accordion_elements.append(
                ui.accordion_panel(
                    ui.output_ui("documents_title"),
                    ui.output_ui("collection_documents"),
                    value="manage_documents",
                )
            )
            items = [
                ui.markdown(
                    f"### Selected collection: {format_collection_name(collections.get().collections[selected_collection.get()], user_info.get())}"
                ),
                ui.accordion(
                    *accordion_elements,
                    id="collection_management_accordion",
                    class_="mb-3",
                ),
            ]
            if "delete" in current_scopes.get():
                items.append(
                    ui.input_task_button(id="delete", label="Delete Collection")
                )
            return ui.TagList(*items)
        if collections.get().loading:
            return ui.markdown("Loading collections...")
        return ui.markdown("Please create a collection first!")

    @render.ui
    def documents_title():
        if fetching_scopes.get():
            return ui.markdown("#### Loading permissions...")
        return ui.markdown("#### Manage Documents")

    @render.ui
    def collection_documents():
        if fetching_scopes.get():
            return ui.markdown("#### Loading permissions...")
        return document_list_ui("document_list")

    @reactive.extended_task
    async def fetch_scopes(collection_id):
        return await client.get_collection_scopes(collection_id)

    @reactive.effect
    def fetch_scopes_effect():
        scopes = fetch_scopes.result()
        current_scopes.set(scopes)
        fetching_scopes.set(False)

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
        fetching_scopes.set(True)
        delete(selected_collection.get())

    @reactive.effect
    @reactive.event(
        selected_collection,
        ignore_none=False,
    )
    def on_collection_change():
        collection_id = selected_collection.get()
        if not collection_id:
            current_scopes.set(set())
            return
        fetching_scopes.set(True)
        fetch_scopes(collection_id)

    @reactive.effect
    @reactive.event(ingestion_done_trigger)
    def on_ingestion_done():
        ui.update_accordion_panel(
            "collection_management_accordion", "manage_documents", show=True
        )

    document_list_server(
        "document_list",
        client,
        selected_collection,
        current_scopes,
        ingestion_done_trigger,
    )
