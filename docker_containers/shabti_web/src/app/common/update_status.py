from shiny import reactive


def update_status_reactives(
    status: reactive.Calc_[dict[str, bool]],
    api_status: reactive.Value,
    opensearch_status: reactive.Value,
    embeddings_status: reactive.Value,
    tika_status: reactive.Value,
):
    current_status = status()
    api_status.set(current_status["api"])
    opensearch_status.set(current_status["opensearch"])
    embeddings_status.set(current_status["embeddings"])
    tika_status.set(current_status["tika"])
