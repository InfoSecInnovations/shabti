import uvicorn
from shabti_util import auth_enabled
from logging_config import logging_config
import os
import argparse
from glob import glob
from multiprocessing import active_children, freeze_support
import requests
import time

if __name__ == "__main__":
    freeze_support()

    parser = argparse.ArgumentParser()
    parser.add_argument("--development", action="store_true")
    command_line_args = parser.parse_args()

    args = {}

    if os.getenv("SHABTI_LOGGING_ENABLED") == "True":
        args["log_config"] = logging_config()

    if auth_enabled():
        args["ssl_keyfile"] = "/api_certs/key.pem"
        args["ssl_certfile"] = "/api_certs/cert.pem"

    if command_line_args.development:
        # keeps the reloader off the .venv sitting in the working directory, see the comment in
        # shabti_web/docker_run.py. Only set alongside reload, which uvicorn warns about otherwise
        args["reload_dirs"] = [
            "/app/shabti/src",
            *sorted(glob("/app/python_packages/*/src")),
        ]

    while True:
        try:
            if (
                requests.get(f"http://{os.getenv('LLM_HOST')}:11434/health").status_code
                == 200
            ):
                break
        except Exception:
            pass
        time.sleep(1)

    try:
        uvicorn.run(
            app="src.app.app:app",
            port=15131,
            host="0.0.0.0",
            reload=command_line_args.development,
            **args,
        )
    except Exception:
        # a failure out of the reload supervisor leaves the spawned server process running and
        # would block here forever joining it, see the comment in shabti_web/docker_run.py
        for child in active_children():
            child.kill()
        raise
