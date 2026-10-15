from shiny import module, reactive, render, ui, Inputs, Outputs, Session
from shabti_types import ProviderInfo, ShabtiError
from shabti_api_client import ShabtiAuthorizationClient, ShabtiRequestError
from ..common.request_errors import request_error_text
from ..common.toasts import show_message

# the credential modes in which users can add keys of their own
USER_KEY_MODES = ("user", "shared_or_user")


def takes_user_keys(provider: ProviderInfo) -> bool:
    return provider.credential_mode in USER_KEY_MODES


def row_id(provider: ProviderInfo) -> str:
    # provider ids are slugs of letters, digits and `-`, which a Shiny id can't have
    return f"row_{provider.id.replace('-', '_')}"


@module.ui
def api_key_row_ui(name: str):
    return ui.card(
        ui.card_header(name),
        ui.output_ui("status"),
        ui.input_password("key", "API Key"),
        ui.div(ui.input_action_button("save", "Save")),
    )


@module.server
def api_key_row_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: ShabtiAuthorizationClient,
    provider: ProviderInfo,
    keys: reactive.Value[dict[str, str] | None],
    keys_changed: reactive.Value[int],
):
    busy = reactive.value(False)

    @reactive.extended_task
    async def save_key(key: str):
        return await client.set_api_key(provider.id, key)

    @reactive.extended_task
    async def remove_key():
        return await client.delete_api_key(provider.id)

    def changed(task: reactive.ExtendedTask, success: str, failure: str):
        try:
            keys.set(task.result())
            # so the prompter lists the models the key gives access to, or stops listing them.
            # isolated, or this effect would depend on the count it bumps
            with reactive.isolate():
                keys_changed.set(keys_changed.get() + 1)
            show_message(success, type="success")
        except (ShabtiRequestError, ShabtiError) as e:
            show_message(f"{failure}: {request_error_text(e)}", type="danger")
        busy.set(False)

    @reactive.effect
    def save_key_effect():
        changed(save_key, f"Saved your {provider.name} key", "Couldn't save the key")

    @reactive.effect
    def remove_key_effect():
        changed(
            remove_key, f"Removed your {provider.name} key", "Couldn't remove the key"
        )

    @reactive.effect
    @reactive.event(input.save, ignore_init=True)
    def on_save():
        key = input.key().strip()
        if busy.get() or not key:
            return
        # cleared straight away, so the key doesn't stay in the page
        ui.update_text("key", value="")
        busy.set(True)
        save_key(key)

    @reactive.effect
    @reactive.event(input.remove, ignore_init=True)
    def on_remove():
        if busy.get() or provider.id not in (keys.get() or {}):
            return
        busy.set(True)
        remove_key()

    # the Remove button is rendered with the status, as there's only something to remove once a
    # key is set. a re-rendered button reports 0, which reactive.event ignores
    @render.ui
    def status():
        found = keys.get()
        if found is None:
            return ui.p("Loading...")
        if provider.id in found:
            return ui.div(
                ui.p(f"Your key: …{found[provider.id]}"),
                ui.input_action_button("remove", "Remove", class_="mb-3"),
            )
        return ui.p(
            "Not set"
            if provider.credential_mode == "user"
            else "Not set, the shared key is used"
        )


@module.ui
def api_keys_ui():
    return [ui.markdown("# API Keys"), ui.output_ui("rows_view")]


@module.server
def api_keys_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: ShabtiAuthorizationClient,
    providers: reactive.Value[list[ProviderInfo] | None],
    keys_changed: reactive.Value[int],
):
    # the last four characters of each key, by provider id. the keys themselves only ever pass
    # through a password input on their way to the API
    keys: reactive.Value[dict[str, str] | None] = reactive.value(None)
    rows: set[str] = set()

    @reactive.extended_task
    async def fetch_keys():
        return await client.get_api_keys()

    @reactive.effect
    def fetch_keys_effect():
        try:
            keys.set(fetch_keys.result())
        except (ShabtiRequestError, ShabtiError) as e:
            keys.set({})
            show_message(
                f"Couldn't get your API keys: {request_error_text(e)}", type="danger"
            )

    @reactive.effect
    def init():
        listed = providers.get()
        if not listed:
            return
        # once each, as the providers are fetched again whenever the API comes back
        for provider in listed:
            if row_id(provider) not in rows:
                rows.add(row_id(provider))
                api_key_row_server(
                    row_id(provider), client, provider, keys, keys_changed
                )
        with reactive.isolate():
            if keys.get() is None:
                fetch_keys()

    # only on the providers, so saving one key doesn't clear what's typed in another row
    @render.ui
    def rows_view():
        return [api_key_row_ui(row_id(x), x.name) for x in providers.get() or []]
