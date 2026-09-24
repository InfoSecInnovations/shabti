import os
from shiny import ui, Inputs, Outputs, Session, reactive, App, render
import shinyswatch
from ..common.collections_data import CollectionsData
from shabti_api_client import ShabtiAuthorizationClient
from shabti_keycloak import get_keycloak_client
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from .app_login import app as app_login
from .oauth2 import auth_callback, refresh, logout
from .auth import get_auth_token
import ssl
from ..common.status import status_ui, status_server
from .home import home_ui, home_server
from ..common.collection_management_ui import collection_management_ui
from .collection_management import collection_management_server
from .files_route import serve_files
from shabti_types import AuthzCollectionInfo
from ..common.prompter import prompter_ui, prompter_server
from .collection_selector_server import collection_selector_server
from .get_api_url import get_api_url
from ..common.update_status import update_status_reactives
from ..common.app_assets import app_assets


app_ui = ui.page_auto(
    app_assets(),
    ui.output_ui("script_output"),
    ui.output_ui("shabti_main"),
    theme=shinyswatch.theme.pulse,
    lang="en",
)

# we're only able to run HTTP in VSCode so we need to allow Oauthlib to use HTTP if we're in the VSCode environment
if os.getenv("VSCODE_INJECTION") == "1":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def server(input: Inputs, output: Outputs, session: Session):
    shinyswatch.theme_picker_server()
    token = get_auth_token(session)
    if not token:
        return
    client = ShabtiAuthorizationClient(
        server_url=get_api_url(),
        token=token,
        keycloak_client=get_keycloak_client(),
        verify=ssl.create_default_context(cafile=os.getenv("ROOT_CA")),
    )
    api_status = reactive.value(False)
    opensearch_status = reactive.value(False)
    llm_status = reactive.value(False)
    tika_status = reactive.value(False)
    selected_collection = reactive.value("")
    collections: reactive.Value[CollectionsData[AuthzCollectionInfo]] = reactive.value(
        CollectionsData(loading=True)
    )
    user_info = reactive.value(None)
    permissions: reactive.Value[set | None] = reactive.value(None)

    @reactive.extended_task
    async def get_info():
        return await client.get_user_info()

    @reactive.effect
    def get_info_effect():
        info = get_info.result()
        user_info.set(info)

    @reactive.extended_task
    async def get_permissions():
        return await client.get_permissions()

    @reactive.effect
    def get_permissions_effect():
        permissions_value = get_permissions.result()
        permissions.set(permissions_value)

    @reactive.effect
    def get_user_data():
        # gate these calls behind the API status otherwise they will fail
        if api_status.get():
            get_info()
            get_permissions()

    @reactive.calc
    def nav_items():
        perms = permissions.get()
        items = [ui.nav_panel("Home", home_ui("home"))]
        if api_status.get() and perms is None:
            items.append(ui.nav_control(ui.markdown("Loading your permissions...")))
        elif perms and (
            "collection:private:create" in perms
            or "collection:shared:create" in perms
            or "update" in perms
            or "delete" in perms
        ):
            items.append(
                ui.nav_panel(
                    "Collection Management",
                    collection_management_ui("collection_management"),
                )
            )
        if perms and "read" in perms:
            items.append(
                ui.nav_panel(
                    "Prompter",
                    prompter_ui("prompter"),
                )
            )
        items.append(
            ui.nav_control(
                ui.input_action_button("openid_logout", "Log Out", class_="my-3")
            )
        )
        return items

    @render.ui
    def shabti_main():
        return ui.navset_pill_list(
            *nav_items(),
            ui.nav_control(status_ui("status_widget"), shinyswatch.theme_picker_ui()),
            id="shabti_nav",
        )

    status = status_server("status_widget", client)

    home_server("home", user_info, permissions, api_status)
    collection_management_server(
        "collection_management",
        client,
        selected_collection,
        collections,
        api_status,
        opensearch_status,
        user_info,
        permissions,
        llm_status,
        tika_status,
    )
    prompter_server(
        "prompter",
        client,
        selected_collection,
        collections,
        api_status,
        opensearch_status,
        llm_status,
        lambda str, selected_collection, collections: collection_selector_server(
            str, selected_collection, collections, user_info
        ),
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
        if api_status.get() and opensearch_status.get():
            fetch_collections()
        else:
            collections.set(CollectionsData(collections={}, loading=False))

    @reactive.effect
    @reactive.event(input.openid_logout, ignore_init=True, ignore_none=True)
    def handle_logout():
        @render.ui
        def shabti_main():
            return ui.tags.script('window.location.href = "/logout"')


shiny_app = App(app_ui, server)

routes = [
    Route("/callback", endpoint=auth_callback),
    Route("/refresh", endpoint=refresh),
    Route("/logout", endpoint=logout),
    Route("/files/{collection_id}/{doc_id}", endpoint=serve_files),
    Mount("/login", app=app_login),
    Mount("/", app=shiny_app),
]


app = Starlette(routes=routes)
