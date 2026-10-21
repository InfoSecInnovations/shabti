import json
from shabti_types import PromptInfo, ModelNotFoundError
from fastapi import HTTPException
from ..functionality.load_prompter_config import load_prompter_config
from ..functionality.chat_model_selection import resolve_prompt_model
from ..functionality.providers.registry import ChatModel


class PromptInfoValidator:
    # returns the chat model the prompt will answer with, or None for a task that won't reach one
    async def __call__(
        self, prompt_info: PromptInfo, credentials: str | None = None
    ) -> ChatModel | None:
        tasks = load_prompter_config("tasks")
        if prompt_info.task not in tasks:
            raise HTTPException(status_code=400, detail="Requested task not found")
        # the model is checked here, as errors can't be reported with a status once the response
        # has started streaming. the task comes first: one without a prompt (`search`) only finds
        # the sources and needs no chat model at all, see docs/developer/PROMPTER.md
        model = None
        if tasks[prompt_info.task].get("prompt"):
            try:
                model = await resolve_prompt_model(credentials, prompt_info.model)
            except ModelNotFoundError as e:
                raise HTTPException(status_code=400, detail=e.message)
            reasoning = prompt_info.reasoning
            if reasoning is not None and reasoning not in model.reasoning:
                allowed = ", ".join(json.dumps(x) for x in model.reasoning)
                raise HTTPException(
                    status_code=400,
                    detail=f"Model {model.id} doesn't take reasoning {json.dumps(reasoning)}"
                    + (f", only {allowed}" if allowed else ""),
                )
        if prompt_info.persona:
            personas = load_prompter_config("personas")
            if prompt_info.persona not in personas:
                raise HTTPException(
                    status_code=400, detail="Requested persona not found"
                )
        if prompt_info.enhancers:
            enhancers = load_prompter_config("enhancers")
            for enhancer in prompt_info.enhancers:
                if enhancer not in enhancers:
                    raise HTTPException(
                        status_code=400, detail="Requested enhancer not found"
                    )
        return model
