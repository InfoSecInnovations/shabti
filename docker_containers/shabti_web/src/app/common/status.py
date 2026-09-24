from shiny import module, reactive, ui, render, Inputs, Outputs, Session, req
from shabti_api_client import BaseShabtiClient
from httpx import TransportError

POLL_SECONDS = 10
# a cold stack comes up in stages - the API, then the LLM host - and at the settled interval a page
# opened during startup waits out a whole tick per stage before anything behind the status, a model
# load in particular, even begins
RETRY_SECONDS = 2


@module.ui
def status_ui():
    return ui.output_ui("status_widget")


@module.server
def status_server(
    input: Inputs, output: Outputs, session: Session, client: BaseShabtiClient
):
    opensearch_status = reactive.value("loading")
    llm_status = reactive.value("loading")
    tika_status = reactive.value("loading")
    api_status = reactive.value("loading")

    @reactive.extended_task
    async def get_llm_status():
        try:
            return "online" if await client.llm_status() else "offline"
        # every way the connection itself can fail, not just a refused one: a cold stack also drops
        # keep-alive connections mid request, and anything escaping here is an unhandled error in
        # the effect below, which takes the whole session down rather than just this poll.
        # Deliberately no wider - a ShabtiRequestError or a malformed body is a real fault
        except TransportError:
            return "loading"

    @reactive.extended_task
    async def get_opensearch_status():
        try:
            return "online" if await client.opensearch_status() else "offline"
        except TransportError:
            return "loading"

    @reactive.extended_task
    async def get_tika_status():
        try:
            return "online" if await client.tika_status() else "offline"
        except TransportError:
            return "loading"

    @reactive.extended_task
    async def get_api_status():
        return "online" if await client.api_status() else "offline"

    @reactive.effect
    def set_llm_status():
        llm_status.set(get_llm_status.result())

    @reactive.effect
    def set_opensearch_status():
        opensearch_status.set(get_opensearch_status.result())

    @reactive.effect
    def set_tika_status():
        tika_status.set(get_tika_status.result())

    @reactive.effect
    def set_api_status():
        api_status.set(get_api_status.result())

    @reactive.effect
    def poll():
        # isolated so the interval is all this read affects: the effect's only dependency has to be
        # its own timer, or a status change would re-run it and stack a timer per poll
        with reactive.isolate():
            settled = api_status.get() == "online"
        reactive.invalidate_later(POLL_SECONDS if settled else RETRY_SECONDS)
        get_api_status()

    # the LLM, OpenSearch and Tika statuses are obtained through the API, so the API needs to be online before we can verify the others
    @reactive.effect
    def on_api_status():
        # this one read is deliberately not isolated: it is what makes this fire the moment the API
        # comes online, rather than waiting out its own timer
        online = api_status.get() == "online"
        with reactive.isolate():
            settled = (
                online
                and llm_status.get() == "online"
                and opensearch_status.get() == "online"
                and tika_status.get() == "online"
            )
        reactive.invalidate_later(POLL_SECONDS if settled else RETRY_SECONDS)
        if online:
            get_llm_status()
            get_opensearch_status()
            get_tika_status()
        else:
            llm_status.set("loading")
            opensearch_status.set("loading")
            tika_status.set("loading")

    @render.ui
    def status_widget():
        items = []
        if api_status.get() != "loading":
            items.append(
                ui.markdown(
                    f"{'🟢' if api_status.get() == 'online' else '🔴'} Shabti API Service"
                )
            )
        if opensearch_status.get() != "loading":
            items.append(
                ui.markdown(
                    f"{'🟢' if opensearch_status.get() == 'online' else '🔴'} OpenSearch"
                )
            )
        if llm_status.get() != "loading":
            items.append(
                ui.markdown(f"{'🟢' if llm_status.get() == 'online' else '🔴'} LLM")
            )
        if tika_status.get() != "loading":
            items.append(
                ui.markdown(f"{'🟢' if tika_status.get() == 'online' else '🔴'} Tika")
            )
        req(items)
        return ui.card(*items)

    @reactive.calc
    def result():
        return {
            "api": api_status.get() == "online",
            "opensearch": opensearch_status.get() == "online",
            "llm": llm_status.get() == "online",
            "tika": tika_status.get() == "online",
        }

    return result
