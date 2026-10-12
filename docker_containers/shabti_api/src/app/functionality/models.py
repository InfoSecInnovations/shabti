import asyncio
import httpx
from shabti_types import ModelLoadInfo, ModelNotFoundError
from httpx_sse import aconnect_sse
import json
from .providers.registry import local_url

# the tags that say what a model is for, as opposed to hints like "default"
CATEGORY_TAGS = ("chat", "embeddings")

# statuses in which a model is occupying, or about to occupy, hardware resources
ACTIVE_STATUSES = ("loaded", "loading", "sleeping")

UNLOAD_POLL_INTERVAL = 0.5
UNLOAD_TIMEOUT = 30


async def get_models(tags: list[str] | None = None, reload: bool = False):
    params = {"reload": 1} if reload else None
    async with httpx.AsyncClient() as client:
        res = (await client.get(local_url("/models"), params=params)).json()
    if tags:
        res["data"] = [
            x for x in res["data"] if any(tag in tags for tag in x.get("tags", []))
        ]
    return res


def model_categories(model) -> set[str]:
    return {tag for tag in model.get("tags", []) if tag in CATEGORY_TAGS}


def model_status(model) -> str | None:
    return model.get("status", {}).get("value")


# takes the output of get_models so the caller can reuse a list it has already fetched
# this function does not check the other tags and will use whichever data is passed in
def default_model_id(models_data) -> str | None:
    models = models_data["data"]
    if not models:
        return None
    # if no model is tagged as the default we just use the first one available
    return next(
        (x["id"] for x in models if "default" in x.get("tags", [])), models[0]["id"]
    )


# takes the output of get_models so the caller can reuse a list it has already fetched
def active_chat_model(models_data) -> str | None:
    model = next(
        (x for x in models_data["data"] if model_status(x) in ACTIVE_STATUSES), None
    )
    return model["id"] if model else None


# yields the ids still waiting to unload on each poll so callers can report progress
async def unload_models(model_ids: list[str]):
    pending = list(model_ids)
    if not pending:
        return
    print(f"unloading {pending}")
    async with httpx.AsyncClient() as client:
        for model_id in pending:
            await client.post(local_url("/models/unload"), json={"model": model_id})

        # the unload response doesn't tell us the resources have actually been released,
        # so we wait for the models to report as unloaded before loading anything else
        waited = 0.0
        while pending:
            yield pending
            await asyncio.sleep(UNLOAD_POLL_INTERVAL)
            waited += UNLOAD_POLL_INTERVAL
            models = (await client.get(local_url("/models"))).json()
            pending = [
                x["id"]
                for x in models["data"]
                if x["id"] in pending and model_status(x) in ACTIVE_STATUSES
            ]
            if pending and waited >= UNLOAD_TIMEOUT:
                raise Exception(f"Timed out unloading {', '.join(pending)}")


# we have no way to size concurrent models against the available hardware yet, so we keep
# at most one model loaded per category and fully swap when a different one is selected
async def unload_conflicting_models(models_data, model_name: str):
    target = next((x for x in models_data["data"] if x["id"] == model_name), None)
    if not target:
        raise ModelNotFoundError(
            model=model_name, message=f"Model {model_name} is not available"
        )
    categories = model_categories(target)
    conflicting = [
        x["id"]
        for x in models_data["data"]
        if x["id"] != model_name
        and model_categories(x) & categories
        and model_status(x) in ACTIVE_STATUSES
    ]
    async for pending in unload_models(conflicting):
        yield ModelLoadInfo(
            progress=0,
            total=1,
            model_name=model_name,
            info=f"unloading {', '.join(pending)}",
        )


async def load_model(model_name: str):
    print(f"loading model {model_name}")

    models_data = await get_models(reload=True)

    # we sweep before checking our own status, as a previous installation may have left a
    # conflicting model loaded even when the one we want is already up
    async for info in unload_conflicting_models(models_data, model_name):
        yield info

    current_status = model_status(
        next(x for x in models_data["data"] if x["id"] == model_name)
    )

    if current_status in ("loaded", "sleeping"):
        print(f"Model {model_name} is already loaded")
        return

    async with httpx.AsyncClient(timeout=None) as httpx_client:
        if current_status == "unloaded":
            await httpx_client.post(
                local_url("/models/load"),
                json={"model": model_name},
            )

        async with aconnect_sse(
            httpx_client,
            "GET",
            local_url("/models/sse"),
        ) as event_source:
            async for sse in event_source.aiter_sse():
                json_data = json.loads(sse.data)
                print(json_data)
                if json_data["model"] != model_name:
                    continue
                status = (
                    "data" in json_data
                    and "status" in json_data["data"]
                    and json_data["data"]["status"]
                )
                if json_data["event"] == "status_change":
                    if status == "loaded":
                        break
                    if status == "unloaded":
                        raise Exception("Model not loaded")
                if status == "loading":
                    yield ModelLoadInfo(
                        progress=json_data["data"]["progress"]["value"],
                        total=1,
                        model_name=model_name,
                        info=f"{status} {json_data['data']['progress']['current']}",
                    )

    print(f"Loaded model {model_name}")
