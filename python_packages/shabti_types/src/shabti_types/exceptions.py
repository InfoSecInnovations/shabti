class ShabtiError(Exception):
    def __init__(self, message="", status=400):
        self.message = message
        self.status = status


class CollectionExistsError(ShabtiError):
    def __init__(self, collection_name, message="", location=None):
        super().__init__(message, 409)
        self.collection_name = collection_name
        self.location = location


class InvalidLocationError(ShabtiError):
    def __init__(self, message='location must be "private" or "shared"'):
        super().__init__(message)


class InvalidUserError(ShabtiError):
    def __init__(self, message=""):
        super().__init__(message)


class UnsupportedFileError(Exception):
    def __init__(self, filename, message=""):
        self.message = message
        self.filename = filename


class ModelNotFoundError(ShabtiError):
    def __init__(self, model="", message=""):
        super().__init__(message)
        self.model = model


class EmbeddingsError(ShabtiError):
    def __init__(self, message="", upstream_status=None):
        super().__init__(message, 502)  # upstream LLM failure, not a client error
        self.upstream_status = upstream_status


class EmptyDocumentError(ShabtiError):
    def __init__(self, source="", message=""):
        super().__init__(message)
        self.source = source


class DuplicateDocumentError(ShabtiError):
    """The collection already holds this content, so this copy was rolled back.

    Always raised with an explicit `message`: `ShabtiError` never calls `Exception.__init__`, so
    `str(e)` on one of these is empty, and `DocumentIngestError.message` is a required string. The
    winning document's id belongs in that message too, because there is nowhere else for an ingest
    item's error to carry it.
    """

    def __init__(self, source="", existing_document_id="", message=""):
        super().__init__(message, 409)
        self.source = source
        self.existing_document_id = existing_document_id


class ForbiddenUrlError(ShabtiError):
    def __init__(self, url="", message=""):
        super().__init__(message, 403)
        self.url = url


class CollectionNotFoundError(ShabtiError):
    def __init__(self, collection_id="", message=""):
        super().__init__(message, 404)
        self.collection_id = collection_id


class IngestNotFoundError(ShabtiError):
    def __init__(self, ingest_id="", message=""):
        # 404 for a foreign ingest as well as a missing one, so ids aren't enumerable
        super().__init__(message, 404)
        self.ingest_id = ingest_id


class EmbeddingsConfigError(ShabtiError):
    """The installer's record of the selected embeddings model is missing or unusable.

    A 500 rather than a 400: nothing the caller sent is wrong, the installation is incomplete.
    """

    def __init__(self, model="", message=""):
        super().__init__(message, 500)
        self.model = model


class EmbeddingsModelMismatchError(ShabtiError):
    """The collection was indexed with a different embeddings model than the one now loaded.

    Its vectors are not comparable with new ones, so ingesting into it would quietly corrupt
    retrieval rather than fail. The embeddings model can only be chosen on a fresh install, so
    this means something has been changed by hand.
    """

    def __init__(self, indexed_model="", current_model="", message=""):
        super().__init__(message, 409)
        self.indexed_model = indexed_model
        self.current_model = current_model
