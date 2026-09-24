from shiny import ui, reactive, render, Inputs, Outputs, Session, module
from .text_input_list import text_input_list
from dataclasses import dataclass, field
from shabti_types import IngestInfo, IngestStatus
from shabti_api_client import BaseShabtiClient, ShabtiRequestError
from .collections_data import CollectionsData
from .ingest_job import (
    collection_label,
    done_message,
    ingest_toast_server,
    ingest_toast_ui,
    is_active,
    item_done,
    item_label,
    progress_rows,
)
from .load_models import load_models
from .toasts import TOAST_POSITION, ProgressToast, show_message
import os
import time

# the API's own listing route anticipates being polled about this often while something is running
POLL_ACTIVE_SECONDS = 1
# idle, this is only watching for a job started from another tab or the CLI, at the rate status.py
# already polls at
POLL_IDLE_SECONDS = 10
# a job that ended just before a reload still gets its outcome announced, which is what the panel's
# five minutes of history used to be for. long enough to survive a refresh someone made because an
# ingest looked stuck, short enough that it isn't reporting news from a previous sitting
FINISHED_ANNOUNCE_SECONDS = 30


@dataclass
class Submission:
    """One POST waiting to be sent.

    The inputs don't block any more, so submissions can arrive faster than the uploads leave. They
    can't all be in flight at once: re-invoking an extended task cancels the call it is already
    making, and for an upload that means losing the file.
    """

    collection_id: str
    files: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)


def just_finished(job: IngestInfo) -> bool:
    return (job.finished or 0) > (time.time() - FINISHED_ANNOUNCE_SECONDS) * 1000


def submit_error_text(error: Exception) -> str:
    if not isinstance(error, ShabtiRequestError):
        return str(error)
    if error.status_code == 403:
        # the wording the collection view already uses for the same denial
        return "You don't have permission to ingest documents into this collection"
    body = error.message["message"]
    return body.get("message") if isinstance(body, dict) else str(body)


@module.ui
def ingester_ui():
    return ui.accordion_panel(
        ui.markdown("#### Ingest Documents"),
        ui.output_ui("ingester_content"),
        value="ingest_documents",
    )


@module.server
def ingester_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    client: BaseShabtiClient,
    selected_collection: reactive.Value,
    collections: reactive.Value[CollectionsData],
    llm_status: reactive.Value,
    tika_status: reactive.Value,
):
    file_input_trigger = reactive.value(0)
    url_input_trigger = reactive.value(0)
    ingesting_done = reactive.value(0)
    embedding_model_loaded = reactive.value(False)
    submit_queue: reactive.Value[list[Submission]] = reactive.value([])
    # bookkeeping nothing renders from, so plain sets rather than reactive values: a toast's
    # contents are pushed to it directly, so nothing here has to invalidate anything
    seen_terminal: set[str] = set()
    job_toasts: dict[str, ProgressToast] = {}
    # per job, every item already flashed: an item's coloured bar, and a failure's own toast, happen
    # on the poll it finished on and on no other - including across a reload part way through a job
    flashed: dict[str, set[str]] = {}
    # ingest ids whose toast is living out one more poll so that whatever finished along with the
    # job still gets seen. the terminal listing travels with them, because by the time we come back
    # the API may have pruned the job or restarted, and the summary has to be right anyway
    pending_close: dict[str, IngestInfo] = {}
    # ingests this session submitted, which is what `flashed` below must not pre-mark: a job we
    # started has no history to skip, so every outcome on it is news - including one that had
    # already failed by the first poll, which is what a duplicate does. it is refused by an id
    # lookup, before the archive probe and before the file is parsed
    submitted: set[str] = set()
    cancelling: set[str] = set()
    active_ingests: set[str] = set()
    polled_once = reactive.value(False)
    # tracked here rather than read off the tasks, because one call at a time is a correctness
    # requirement for both of them: re-invoking an extended task cancels the call it is already
    # making, and for an upload that means losing the file
    polling = reactive.value(False)
    submitting = reactive.value(False)

    @render.ui
    def ingester_content():
        if not llm_status.get():
            return ui.markdown("Waiting for LLM host to come online...")
        if not tika_status.get():
            return ui.markdown("Waiting for Tika to come online...")
        if not embedding_model_loaded.get():
            return ui.markdown("Loading embeddings model...")
        return ui.TagList(
            ui.markdown("#### Files"),
            ui.output_ui("file_input"),
            ui.markdown("#### URLs"),
            ui.output_ui("url_input"),
            # not a task button: with nothing to bind it to it would enter its busy state on click
            # and never leave it. the toasts are the feedback now
            ui.input_action_button(id="ingest", label="Ingest"),
        )

    @render.ui
    @reactive.event(file_input_trigger, ignore_none=False, ignore_init=False)
    def file_input():
        return ui.input_file(id="ingester_files", label=None, multiple=True)

    @render.ui
    @reactive.event(url_input_trigger, ignore_none=False, ignore_init=False)
    def url_input():
        return text_input_list("url_input_list")

    def toast_id(ingest_id: str) -> str:
        return f"ingest-{ingest_id}"

    async def show_job_toast(job: IngestInfo):
        toast = ProgressToast(
            toast_id(job.ingest_id),
            "Ingesting documents",
            ingest_toast_ui(job.ingest_id),
            # not dismissable while it runs, because it holds the only Cancel button there is
            closable=False,
        )
        job_toasts[job.ingest_id] = toast
        # a job met part way through - another tab, the CLI, or a reload during a long ingest -
        # must not replay a bar and an error toast for every item that finished before this tab was
        # open, so its history counts as already flashed. one this session submitted has no history
        # to replay, and an item of it can already have failed by the time of the first poll
        flashed[job.ingest_id] = (
            set()
            if job.ingest_id in submitted
            else {i.item_id for i in job.items if item_done(i)}
        )
        # the server before the toast goes up: it registers the Cancel button's handlers
        # synchronously, so the input exists by the time the client binds to it
        ingest_toast_server(job.ingest_id, client, job.ingest_id, toast, cancelling)
        await update_job_toast(job)

    def flash_item_errors(job: IngestInfo) -> set[str]:
        """The items that finished since the last poll, each failure getting a toast of its own.

        Separate from the progress toast because a job can be over before a poll ever saw it
        running - one refused file is - and its failures are no less worth reporting for that.
        Returned as well as acted on: the coloured bars flash the same set.
        """
        done_now = {item.item_id for item in job.items if item_done(item)}
        # `item_done` only ever goes from false to true, so an item is flashed exactly once
        flashing = done_now - flashed.setdefault(job.ingest_id, set())
        flashed[job.ingest_id] = done_now
        for item in job.items:
            if item.item_id in flashing and item.error is not None:
                # its own toast, and one that stays until it is clicked away: a failure that only
                # flashed for a poll would be missed by anyone not watching at that moment
                show_message(
                    f"{item_label(item)}: {item.error.message or item.error.error}",
                    type="danger",
                    duration_s=None,
                )
        return flashing

    async def update_job_toast(job: IngestInfo):
        flashing = flash_item_errors(job)
        rows, status = progress_rows(
            job,
            job.ingest_id in cancelling,
            collection_label(job, collections, selected_collection),
            flashing,
        )
        # the first call is what puts the toast up, so the Cancel button and the bars arrive
        # together rather than the toast flashing empty
        await job_toasts[job.ingest_id].set_rows(rows, status)

    def announce_done(job: IngestInfo):
        message, kind = done_message(
            job, collection_label(job, collections, selected_collection)
        )
        # the shape load_models already uses: something to watch while it runs, then a brief word
        # once it is over. under its own id, because the progress toast's was just taken down
        ui.show_toast(
            ui.toast(
                message,
                id=f"{toast_id(job.ingest_id)}-done",
                type=kind,
                duration_s=5,
                position=TOAST_POSITION,
            )
        )

    def close_job_toast(job: IngestInfo):
        job_toasts.pop(job.ingest_id).hide()
        cancelling.discard(job.ingest_id)
        flashed.pop(job.ingest_id, None)
        announce_done(job)

    @reactive.extended_task
    async def fetch_ingests() -> list[IngestInfo] | None:
        try:
            return await client.get_ingests()
        except Exception:
            # anything at all, because this is what clears `polling`: a listing that raised out of
            # here would stop the poll for the rest of the session. the API going away is ordinary
            # during a restart, and the status widget already says so
            return None

    def poll_now():
        # a tick landing while the API is still answering is skipped rather than taken, or a slow
        # listing would be cancelled and retried forever and never return anything
        with reactive.isolate():
            if polling.get():
                return
            polling.set(True)
        fetch_ingests()

    @reactive.effect
    def poll_ingests():
        # `active_ingests` is a plain set rather than a reactive value for this reason: the effect's
        # only dependency has to be its own timer, or a poll result would re-run it and stack a
        # timer per poll
        interval = (
            POLL_ACTIVE_SECONDS
            if active_ingests or pending_close
            else POLL_IDLE_SECONDS
        )
        poll_now()
        reactive.invalidate_later(interval)

    @reactive.effect
    async def ingests_effect():
        latest = fetch_ingests.result()
        with reactive.isolate():
            polling.set(False)
            # the poll before this one was some job's last. its toast was left up for the interval
            # so that whatever finished with it got seen, and it comes down now - from the listing
            # we kept rather than from this one, so a job the API has since pruned, or lost to a
            # restart, still gets its summary and still lets the poll drop back to idle
            for finished in pending_close.values():
                close_job_toast(finished)
            pending_close.clear()
        if latest is None:
            # the API went away mid poll. a listing we never got is not evidence that anything
            # finished, still less that anything was pruned, so leave every toast where it is
            return
        with reactive.isolate():
            first_poll = not polled_once.get()
            for job in latest:
                if is_active(job):
                    if job.ingest_id in job_toasts:
                        await update_job_toast(job)
                    elif job.ingest_id not in seen_terminal:
                        # only a job that is running when we first see it gets a progress toast:
                        # the API keeps a finished ingest for an hour, and a page load must not pop
                        # one toast per job that ended before the tab was even open
                        await show_job_toast(job)
                    continue
                # checked before `seen_terminal` is added to below, or the toast for a job that has
                # just finished would never be taken down
                if job.ingest_id in job_toasts:
                    if job.status == IngestStatus.CANCELLED:
                        # nothing left to flash: a cancelled job's unfinished items were rolled
                        # back, so holding its toast open would only prolong a stale status line
                        close_job_toast(job)
                    else:
                        # one more update rather than the close, because the items that finished
                        # along with the job are only being reported now, and a bar nobody saw is
                        # the whole point of this
                        await update_job_toast(job)
                        pending_close[job.ingest_id] = job
                elif job.ingest_id not in seen_terminal and (
                    job.ingest_id in submitted or (first_poll and just_finished(job))
                ):
                    # over before any poll saw it running, which one refused file is: no toast ever
                    # went up for it, so this is the only chance its failures have to be seen
                    flash_item_errors(job)
                    flashed.pop(job.ingest_id, None)
                    announce_done(job)
                if job.ingest_id in seen_terminal:
                    continue
                seen_terminal.add(job.ingest_id)
                submitted.discard(job.ingest_id)
                # the first poll of a session sees up to an hour of finished jobs. refreshing for
                # those would force the documents panel open on every single page load
                if not first_poll and job.collection_id == selected_collection.get():
                    ingesting_done.set(ingesting_done.get() + 1)
            # a job the API pruned, or one lost across an API restart, never reports a terminal
            # status, so its toast's only remaining signal is the listing no longer mentioning it
            listed = {job.ingest_id for job in latest}
            for ingest_id in [i for i in job_toasts if i not in listed]:
                job_toasts.pop(ingest_id).hide()
                cancelling.discard(ingest_id)
                flashed.pop(ingest_id, None)
                pending_close.pop(ingest_id, None)
                submitted.discard(ingest_id)
                seen_terminal.add(ingest_id)
            active_ingests.clear()
            active_ingests.update(job.ingest_id for job in latest if is_active(job))
            polled_once.set(True)

    @reactive.extended_task
    async def start_ingests(
        submissions: list[Submission],
    ) -> tuple[list[str], list[str]]:
        started = []
        errors = []
        for submission in submissions:
            try:
                if submission.files:
                    ingest = await client.start_files_ingest(
                        submission.collection_id, submission.files
                    )
                else:
                    ingest = await client.start_urls_ingest(
                        submission.collection_id, submission.urls
                    )
                # what tells the polling below that this job is ours, and so that nothing on it has
                # been reported yet however far along it is by the time we first see it
                started.append(ingest.ingest_id)
            except Exception as e:
                # per submission, and anything at all: one failure must not take the rest of the
                # queue with it, nor leave `submitting` stuck and block every later one. a denial
                # arrives here now rather than on the first line of the stream this used to return
                errors.append(submit_error_text(e))
        return started, errors

    @reactive.effect
    def drain_submit_queue():
        # woken by a new submission and again when the POST before it finishes
        if submitting.get():
            return
        queued = submit_queue.get()
        if not queued:
            return
        with reactive.isolate():
            submit_queue.set([])
            submitting.set(True)
        start_ingests(queued)

    @reactive.effect
    def start_ingests_effect():
        started, errors = start_ingests.result()
        with reactive.isolate():
            submitting.set(False)
            submitted.update(started)
        for message in errors:
            show_message(message, type="danger")
        # rather than waiting out the poll interval to show what was just submitted
        poll_now()

    @reactive.effect
    @reactive.event(input.ingester_files, ignore_none=True, ignore_init=True)
    def handle_file_upload():
        files = input.ingester_files()
        if not len(files):
            return
        # we want to conserve the original file names
        named_files = []
        for file in files:
            named_file = os.path.join(os.path.dirname(file["datapath"]), file["name"])
            os.rename(file["datapath"], named_file)
            named_files.append(named_file)
        collection_id = selected_collection.get()
        print(f"ingesting documents into collection {collection_id}")
        # the input keeps whatever was chosen, so it is replaced to let the same file be picked
        # twice. blanking it out during the ingest used to do this
        file_input_trigger.set(file_input_trigger.get() + 1)
        submit_queue.set(
            [*submit_queue.get(), Submission(collection_id, files=named_files)]
        )
        # the input has already been cleared and the upload itself can take a while, so without
        # this there would be nothing at all on screen until the POST lands and the next poll runs
        show_message("Submitting ingest...", duration_s=3)

    @reactive.effect
    @reactive.event(input.ingest, ignore_none=False, ignore_init=True)
    def handle_url_ingest_click():
        urls = input.url_input_list()
        if not urls or not len(urls):
            return
        collection_id = selected_collection.get()
        print(f"ingesting documents into collection {collection_id}")
        # replaced to clear the submitted URLs, which blanking the list out used to do
        url_input_trigger.set(url_input_trigger.get() + 1)
        submit_queue.set([*submit_queue.get(), Submission(collection_id, urls=urls)])
        show_message("Submitting ingest...", duration_s=3)

    @reactive.extended_task
    async def load_embedding_model():
        models = await client.get_models()
        embeddings = next(m for m in models["data"] if "embeddings" in m["tags"])
        await load_models(client, embeddings["id"])

    @reactive.effect
    def load_model_effect():
        load_embedding_model.result()
        embedding_model_loaded.set(True)

    @reactive.effect
    def init():
        if llm_status.get() and not embedding_model_loaded.get():
            load_embedding_model()

    return ingesting_done
