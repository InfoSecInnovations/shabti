from shiny import module, reactive, ui, Inputs, Outputs, Session
from ..common.collections_data import CollectionsData
from shabti_types import CollectionExistsError, AuthzCollectionInfo, ShabtiError
from shabti_api_client import ShabtiAuthorizationClient, ShabtiRequestError
from ..common.request_errors import request_error_text
from ..common.text_input_enter import text_input_enter_ui, text_input_enter_server
from ..common.toasts import show_message

COLLECTION_PLACEHOLDER = "new_collection_name"


@module.ui
def collection_create_ui(show_toggle):
    elements = [
        text_input_enter_ui("new_collection", "New Collection", COLLECTION_PLACEHOLDER)
    ]
    if show_toggle:
        elements.append(ui.input_checkbox("toggle_shared", "Shared Collection"))
    return elements


@module.server
def collection_create_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: ShabtiAuthorizationClient,
    selected_collection: reactive.Value,
    collections: reactive.Value[CollectionsData[AuthzCollectionInfo]],
    permissions: reactive.Value[set],
):
    creating = reactive.value(False)
    new_collection_name = text_input_enter_server("new_collection", creating)

    @reactive.extended_task
    async def create_shabti_collection(collection_name: str, location: str):
        collection_id = await client.create_collection(collection_name, location)
        new_collections = await client.get_collections()
        return (collection_id, new_collections)

    @reactive.effect
    def create_collection_effect():
        try:
            collection_id, new_collections = create_shabti_collection.result()
            collections.set(
                CollectionsData(
                    collections={
                        collection.collection_id: collection
                        for collection in new_collections
                    },
                    loading=False,
                )
            )
            selected_collection.set(collection_id)
        except CollectionExistsError:
            show_message("Collection with this name already exists", type="danger")
        # creating one embeds a probe text, so the embeddings model being unavailable or rate
        # limited is reported here, and would otherwise take the session down
        except (ShabtiRequestError, ShabtiError) as e:
            show_message(
                f"Couldn't create the collection: {request_error_text(e)}",
                type="danger",
            )
        creating.set(False)

    @reactive.effect
    @reactive.event(new_collection_name, ignore_none=True, ignore_init=True)
    def on_create():
        new_name = new_collection_name()
        if not new_name:
            return
        perms = permissions.get()
        if "collection:private:create" not in perms:
            location = "shared"
        elif "collection:shared:create" not in perms:
            location = "private"
        else:
            location = "shared" if input.toggle_shared() else "private"
        creating.set(True)
        create_shabti_collection(new_name, location)
