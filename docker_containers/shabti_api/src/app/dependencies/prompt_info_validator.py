from shabti_types import PromptInfo, ModelNotFoundError
from fastapi import HTTPException
from ..functionality.load_prompter_config import load_prompter_config
from ..functionality.models import get_loaded_chat_model


class PromptInfoValidator:
    async def __call__(self, prompt_info: PromptInfo):
        tasks = load_prompter_config("tasks")
        if prompt_info.task not in tasks:
            raise HTTPException(status_code=400, detail="Requested task not found")
        # the prompt runs on whichever chat model is loaded, so we check here that there
        # is one, as errors can't be reported once the response has started streaming.
        # the task comes first: one without a prompt (`search`) only finds the sources and
        # needs no chat model at all, see docs/developer/PROMPTER.md
        if tasks[prompt_info.task].get("prompt"):
            try:
                await get_loaded_chat_model()
            except ModelNotFoundError as e:
                raise HTTPException(status_code=400, detail=e.message)
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
