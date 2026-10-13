from shiny import module, reactive, req, Inputs, Outputs, Session
from ..common.collections_data import CollectionsData
from shabti_types import CollectionExistsError, ShabtiError
from shabti_api_client import ShabtiClient, ShabtiRequestError
from ..common.request_errors import request_error_text
from ..common.text_input_enter import text_input_enter_ui, text_input_enter_server
from ..common.toasts import show_message
from shabti_types import CollectionInfo

COLLECTION_PLACEHOLDER = "new_collection_name"


@module.ui
def collection_create_ui():
    return [
        text_input_enter_ui("new_collection", "New Collection", COLLECTION_PLACEHOLDER)
    ]


@module.server
def collection_create_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: ShabtiClient,
    selected_collection: reactive.Value,
    collections: reactive.Value[CollectionsData[CollectionInfo]],
):
    creating = reactive.value(False)
    new_collection_name = text_input_enter_server("new_collection", creating)

    @reactive.extended_task
    async def create_shabti_collection(collection_name):
        collection_id = await client.create_collection(collection_name)
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
        req(new_collection_name())
        new_name = new_collection_name()
        if not new_name:
            return
        creating.set(True)
        create_shabti_collection(new_name)
