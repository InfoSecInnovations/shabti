import os
import secrets

filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


async def test_create_collection(shabti_client):
    collection_name = secrets.token_hex(8)
    collection_id = await shabti_client.create_collection(collection_name)
    assert collection_id
    collections = await shabti_client.get_collections()
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == collection_id
            and collection_info.collection_name == collection_name
        ),
        None,
    )


async def test_list_collections(shabti_client, shabti_collection_id):
    collections = await shabti_client.get_collections()
    assert next(
        (
            collection
            for collection in collections
            if collection.collection_id == shabti_collection_id
        ),
        None,
    )


async def test_ingest_document(shabti_client, shabti_collection_id):
    document_id = None
    async for info in shabti_client.insert_files(shabti_collection_id, [file_path]):
        document_id = info.document_id
    assert document_id
    docs = await shabti_client.get_documents(shabti_collection_id)
    assert next(
        (
            doc
            for doc in docs.documents
            if doc.filename == filename and doc.document_id == document_id
        ),
        None,
    )


async def test_ingest_url(shabti_client, shabti_collection_id):
    document_id = None
    url = "https://www.scrapethissite.com/pages/simple/"
    async for info in shabti_client.insert_urls(shabti_collection_id, [url]):
        document_id = info.document_id
    assert document_id
    docs = await shabti_client.get_documents(shabti_collection_id)
    assert next(
        (
            doc
            for doc in docs.documents
            if doc.source == url and doc.document_id == document_id
        ),
        None,
    )


async def test_list_documents(shabti_client, shabti_collection_id, shabti_document_id):
    docs = await shabti_client.get_documents(shabti_collection_id)
    assert next(
        (doc for doc in docs.documents if doc.document_id == shabti_document_id), None
    )


async def test_delete_document(shabti_client, shabti_collection_id, shabti_document_id):
    await shabti_client.delete_document(shabti_collection_id, shabti_document_id)
    docs = await shabti_client.get_documents(shabti_collection_id)
    assert not any(doc.document_id == shabti_document_id for doc in docs.documents)


async def test_delete_collection(shabti_client, shabti_collection_id):
    await shabti_client.delete_collection(shabti_collection_id)
    collections = await shabti_client.get_collections()
    assert not any(
        collection.collection_id == shabti_collection_id for collection in collections
    )


async def test_llm_status(shabti_client):
    assert await shabti_client.llm_status()


async def test_embeddings_status(shabti_client):
    assert await shabti_client.embeddings_status()


async def test_opensearch_status(shabti_client):
    assert await shabti_client.opensearch_status()


async def test_tika_status(shabti_client):
    assert await shabti_client.tika_status()


async def test_api_status(shabti_client):
    assert await shabti_client.api_status()


# if nothing errors the test will pass
async def test_prompting(shabti_client, shabti_collection_id, loaded_chat_model):
    filename = "prompt_test.md"
    file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)
    async for info in shabti_client.insert_files(shabti_collection_id, [file_path]):
        pass
    async for response in shabti_client.prompt(
        shabti_collection_id,
        "What does the word prompting mean?",
        "question",
    ):
        pass


async def test_prompting_with_a_named_model(
    shabti_client, shabti_collection_id, loaded_chat_model
):
    filename = "prompt_test.md"
    file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)
    async for info in shabti_client.insert_files(shabti_collection_id, [file_path]):
        pass
    chunks = [
        chunk
        async for chunk in shabti_client.prompt(
            shabti_collection_id,
            "What does the word prompting mean?",
            "question",
            model=f"local/{loaded_chat_model}",
        )
    ]
    assert any(chunk.response for chunk in chunks)
    assert not any(chunk.error for chunk in chunks)


# the stub provider is the test llama.cpp configured as a remote one would be, see testing/stack.ts
async def test_prompting_with_a_remote_model(
    shabti_client, shabti_collection_id, loaded_chat_model
):
    filename = "prompt_test.md"
    file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)
    async for info in shabti_client.insert_files(shabti_collection_id, [file_path]):
        pass
    chunks = [
        chunk
        async for chunk in shabti_client.prompt(
            shabti_collection_id,
            "What does the word prompting mean?",
            "question",
            model=f"stub/{loaded_chat_model}",
        )
    ]
    assert any(chunk.response for chunk in chunks)
    assert not any(chunk.error for chunk in chunks)


async def test_get_providers(shabti_client):
    providers = {x.id: x for x in await shabti_client.get_providers()}
    assert providers["local"].credential_mode == "none"
    assert providers["stub"].has_shared_key


async def test_get_provider_models(shabti_client, loaded_chat_model):
    models = {x.id: x for x in await shabti_client.get_provider_models("chat")}
    assert models[f"local/{loaded_chat_model}"].status == "loaded"
    assert models[f"stub/{loaded_chat_model}"].model_id == loaded_chat_model
    assert all(x.category == "chat" for x in models.values())
    # it only takes users' own keys, which nobody has with security disabled
    assert not any(x.provider_id == "stub-byok" for x in models.values())


async def test_provider_status(shabti_client):
    assert await shabti_client.provider_status() == {
        "local": True,
        "stub": True,
        "stub-byok": True,
    }


async def test_get_chat_model_selection(shabti_client, loaded_chat_model):
    # with security disabled the selection is whichever local model is loaded
    assert (
        await shabti_client.get_chat_model_selection() == f"local/{loaded_chat_model}"
    )
