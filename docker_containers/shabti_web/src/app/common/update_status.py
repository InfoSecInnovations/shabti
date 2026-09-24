from shiny import reactive


def update_status_reactives(
    status: reactive.Calc_[dict[str, bool]],
    api_status: reactive.Value,
    opensearch_status: reactive.Value,
    llm_status: reactive.Value,
    tika_status: reactive.Value,
):
    current_status = status()
    api_status.set(current_status["api"])
    opensearch_status.set(current_status["opensearch"])
    llm_status.set(current_status["llm"])
    tika_status.set(current_status["tika"])
