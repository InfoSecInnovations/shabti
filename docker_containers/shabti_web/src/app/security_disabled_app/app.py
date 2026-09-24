from shiny import ui, Inputs, Outputs, Session, reactive, render, App
import shinyswatch
from shabti_api_client import ShabtiClient
from ..common.collections_data import CollectionsData
from ..common.status import status_ui, status_server
from .home import home_ui, home_server
from .collection_management import collection_management_server
from ..common.collection_management_ui import collection_management_ui
from ..common.update_status import update_status_reactives
from ..common.app_assets import app_assets
from ..common.prompter import prompter_ui, prompter_server
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from .files_route import serve_files
from shabti_types import CollectionInfo
from .collection_selector_server import collection_selector_server
from .get_api_url import get_api_url

app_ui = ui.page_auto(
    app_assets(),
    ui.output_ui("shabti_main"),
    theme=shinyswatch.theme.pulse,
    lang="en",
)


def server(input: Inputs, output: Outputs, session: Session):
    shinyswatch.theme_picker_server()
    client = ShabtiClient(get_api_url())
    api_status = reactive.value(False)
    opensearch_status = reactive.value(False)
    llm_status = reactive.value(False)
    tika_status = reactive.value(False)
    selected_collection = reactive.value("")
    collections: reactive.Value[CollectionsData[CollectionInfo]] = reactive.value(
        CollectionsData(loading=True)
    )

    @render.ui
    def shabti_main():
        return ui.navset_pill_list(
            ui.nav_panel("Home", home_ui("home")),
            ui.nav_panel(
                "Collection Management",
                collection_management_ui("collection_management"),
            ),
            ui.nav_panel("Prompter", prompter_ui("prompter")),
            ui.nav_control(status_ui("status_widget"), shinyswatch.theme_picker_ui()),
            id="shabti_nav",
        )

    home_server("home", api_status)
    status = status_server("status_widget", client)
    collection_management_server(
        "collection_management",
        client,
        selected_collection,
        collections,
        api_status,
        opensearch_status,
        llm_status,
        tika_status,
    )

    @reactive.effect
    def update_status():
        update_status_reactives(
            status, api_status, opensearch_status, llm_status, tika_status
        )

    @reactive.extended_task
    async def fetch_collections():
        return await client.get_collections()

    @reactive.effect
    def fetch_collections_effect():
        new_collections = fetch_collections.result()
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
    def update_collections():
        if opensearch_status.get():
            fetch_collections()
        else:
            collections.set(CollectionsData(collections={}, loading=False))

    prompter_server(
        "prompter",
        client,
        selected_collection,
        collections,
        api_status,
        opensearch_status,
        llm_status,
        collection_selector_server,
    )


shiny_app = App(app_ui, server)

routes = [
    Route("/files/{collection_id}/{doc_id}", endpoint=serve_files),
    Mount("/", app=shiny_app),
]


app = Starlette(routes=routes)
