import asyncio
import os
from opensearchpy import AsyncOpenSearch


MAPPING_INDEX_NAME = "collection_mappings"
FILES_INDEX_NAME = "file_mappings"
OPENSEARCH_MAX_RESULTS = 10000
# the embeddings model's output size: an index built at one dimension cannot accept vectors of
# another, so swapping the model without changing this silently breaks indexing
VECTOR_DIMENSION = 768

_clients: dict[asyncio.AbstractEventLoop, AsyncOpenSearch] = {}


def get_client() -> AsyncOpenSearch:
    # cached rather than built per call so its connection pool is actually reused: ingesting a
    # large document makes thousands of calls, and a fresh client per call reconnects for every
    # one. it can't be one client for the whole process either, because the aiohttp session
    # underneath is created on the first request and stays bound to the loop that was running
    # then, and any other loop then fails on timers and futures it doesn't own
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        # a closed loop can no longer run its client's shutdown, so drop the entry and leave the
        # session to the garbage collector
        for closed in [key for key in _clients if key.is_closed()]:
            del _clients[closed]
        port = 9200
        client = AsyncOpenSearch(
            hosts=[{"host": os.getenv("OPENSEARCH_HOST", "localhost"), "port": port}],
            use_ssl=False,
        )
        _clients[loop] = client
    return client


async def close_client():
    # closing has to happen on the loop that created the session, so this is a coroutine rather
    # than something an exit handler could call
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.close()


async def create_collection_index(collection_id):
    client = get_client()
    collection_index_name = collection_id
    collection_index_body = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "document_vector": {
                    "type": "knn_vector",
                    "dimension": VECTOR_DIMENSION,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "lucene",
                        "parameters": {},
                    },
                },
                "page_id": {"type": "keyword"},
                "filename": {"type": "wildcard"},
                "source": {"type": "wildcard"},
                "media_type": {"type": "keyword"},
                "ingest_date": {"type": "unsigned_long"},
                "languages": {"type": "keyword"},
                "text": {"type": "text"},
                "binary_path": {"type": "keyword"},
                "page_number": {"type": "integer"},
                "type": {"type": "keyword"},
                "child_item_to_document": {
                    "type": "join",
                    "relations": {"document": "child_item"},
                },
            }
        },
    }
    await client.indices.create(index=collection_index_name, body=collection_index_body)


async def create_index_mapping(collection_id, collection_name):
    client = get_client()
    if not await client.indices.exists(index=MAPPING_INDEX_NAME):
        index_body = {
            "mappings": {"properties": {"collection_name": {"type": "keyword"}}}
        }
        await client.indices.create(index=MAPPING_INDEX_NAME, body=index_body)
    await client.index(
        index=MAPPING_INDEX_NAME,
        body={"collection_name": collection_name},
        id=collection_id,
        refresh=True,
    )


async def delete_index_mapping(collection_id):
    client = get_client()
    await client.delete(index=MAPPING_INDEX_NAME, id=collection_id, refresh=True)


async def get_collection_mappings():
    client = get_client()
    if not await client.indices.exists(index=MAPPING_INDEX_NAME):
        return []
    query = {
        "size": OPENSEARCH_MAX_RESULTS,
        "query": {"match_all": {}},
    }
    response = await client.search(body=query, index=MAPPING_INDEX_NAME)
    collections = [
        {
            "collection_name": hit["_source"]["collection_name"],
            "collection_id": hit["_id"],
        }
        for hit in response["hits"]["hits"]
    ]
    return collections


async def get_collection_mapping(collection_name: str):
    client = get_client()
    if not await client.indices.exists(index=MAPPING_INDEX_NAME):
        return None
    query = {
        "size": 1,
        "query": {"bool": {"filter": [{"term": {"collection_name": collection_name}}]}},
    }
    response = await client.search(body=query, index=MAPPING_INDEX_NAME)
    ids = [hit["_id"] for hit in response["hits"]["hits"]]
    if ids:
        return ids[0]
    return None


async def get_opensearch_collection_info(collection_id: str):
    client = get_client()
    if not await client.indices.exists(index=MAPPING_INDEX_NAME):
        return None
    item = await client.get(index=MAPPING_INDEX_NAME, id=collection_id)
    return {
        "collection_id": item["_id"],
        "collection_name": item["_source"]["collection_name"],
    }


async def freeze_collection_and_get_file_paths(collection_id: str):
    client = get_client()
    paths = []
    await client.indices.add_block(
        index=collection_id, block="write"
    )  # avoid additional writes to the index while we retrieve the list of file paths
    pit_id = (await client.create_pit(index=collection_id, keep_alive="100m"))["pit_id"]
    body = {
        "_source": {"includes": ["binary_path"]},
        "size": OPENSEARCH_MAX_RESULTS,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"type": "document"}},
                    {"exists": {"field": "binary_path"}},
                ]
            }
        },
        "pit": {"id": pit_id, "keep_alive": "100m"},
        "sort": [{"ingest_date": {"order": "asc"}}],
    }
    response = await client.search(body=body)
    while len(response["hits"]["hits"]):
        paths = [
            *paths,
            *[hit["_source"]["binary_path"] for hit in response["hits"]["hits"]],
        ]
        body["search_after"] = response["hits"]["hits"][-1]["sort"]
        response = await client.search(body=body)
    return paths


async def delete_collection_indices(collection_id: str):
    client = get_client()
    response = await client.indices.delete(index=collection_id)
    if not response["acknowledged"]:
        print(f"Failed to delete indices for {collection_id}")
        return False
    return True


async def get_document_counts(collection_id: str, doc_ids: list[str]):
    # one aggregation for the whole page of documents. these counts used to be a pair of count
    # queries per document, so listing N documents cost 2N round trips
    counts = {doc_id: {"page_count": 0, "vector_count": 0} for doc_id in doc_ids}
    if not doc_ids:
        return counts
    client = get_client()
    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"terms": {"type": ["page", "vector"]}},
                    {
                        "has_parent": {
                            "parent_type": "document",
                            "query": {"ids": {"values": doc_ids}},
                        }
                    },
                ]
            }
        },
        "aggs": {
            "document": {
                # the join field's parent id, the only thing pages and vectors both carry: pages
                # have no doc_id field of their own
                "terms": {
                    "field": "child_item_to_document#document",
                    "size": len(doc_ids),
                },
                "aggs": {"type": {"terms": {"field": "type", "size": 2}}},
            }
        },
    }
    response = await client.search(body=body, index=collection_id)
    for bucket in response["aggregations"]["document"]["buckets"]:
        doc_counts = counts.get(bucket["key"])
        if doc_counts is None:
            continue
        for type_bucket in bucket["type"]["buckets"]:
            doc_counts[f"{type_bucket['key']}_count"] = type_bucket["doc_count"]
    return counts


async def add_document_metadata(collection_id, doc):
    doc.update((await get_document_counts(collection_id, [doc["id"]]))[doc["id"]])
    return doc


async def get_document(collection_id: str, doc_id: str):
    client = get_client()
    item = await client.get(index=collection_id, id=doc_id)
    doc = {**item["_source"], "id": item["_id"]}
    doc = await add_document_metadata(collection_id, doc)
    return doc


async def get_document_file_path(collection_id: str, doc_id: str):
    client = get_client()
    item = await client.get(index=collection_id, id=doc_id)
    if "binary_path" in item["_source"]:
        return item["_source"]["binary_path"]
    return None


async def get_opensearch_documents(
    collection_id: str, search, sort, max_results, filter_document_type, page=0
):
    client = get_client()
    if not search:
        filter = [{"term": {"type": "document"}}]
        if filter_document_type:
            filter.append({"terms": {"media_type": filter_document_type}})
        body = {
            "size": max_results or OPENSEARCH_MAX_RESULTS,
            "query": {"bool": {"filter": filter}},
        }
    else:
        body = {
            "_source": {"excludes": ["document_vector"]},
            "size": max_results or OPENSEARCH_MAX_RESULTS,
            "query": {
                "bool": {
                    "should": [
                        {
                            "bool": {
                                "boost": 100,
                                "minimum_should_match": 1,
                                "should": [
                                    {"wildcard": {"filename": f"*{search}*"}},
                                    {"wildcard": {"source": f"*{search}*"}},
                                    {"term": {"_id": {"value": search}}},
                                ],
                                "filter": {"term": {"type": "document"}},
                            }
                        },
                        {
                            "bool": {
                                "must": [
                                    {
                                        "has_child": {
                                            "score_mode": "max",
                                            "type": "child_item",
                                            "query": {"match": {"text": search}},
                                        }
                                    }
                                ]
                            }
                        },
                    ]
                }
            },
        }
        if filter_document_type:
            # a bool with nothing but `should` requires one of them to match, but adding a filter
            # drops that requirement to zero and leaves the search clauses only scoring what the
            # filter already let through. saying so explicitly keeps the search a search
            body["query"]["bool"]["minimum_should_match"] = 1
            body["query"]["bool"]["filter"] = [
                {"terms": {"media_type": filter_document_type}}
            ]
    if max_results and page:
        body["from"] = max_results * page
    if sort:
        if sort == "date_asc":
            body["sort"] = {"ingest_date": {"order": "asc"}}
        if sort == "date_desc":
            body["sort"] = {"ingest_date": {"order": "desc"}}
    response = await client.search(body=body, index=collection_id)
    hits = response["hits"]["hits"]
    counts = await get_document_counts(collection_id, [hit["_id"] for hit in hits])
    docs = [{**hit["_source"], "id": hit["_id"], **counts[hit["_id"]]} for hit in hits]
    body = {"query": {"bool": {"filter": [{"term": {"type": "document"}}]}}}
    count_response = await client.count(body=body, index=collection_id)

    return {
        "documents": docs,
        "total_hits": response["hits"]["total"]["value"],
        "total_documents": count_response["count"],
    }


async def get_opensearch_document_types(collection_id: str):
    client = get_client()
    body = {
        "size": 0,
        "aggs": {
            "document_type": {
                "terms": {"field": "media_type", "size": OPENSEARCH_MAX_RESULTS}
            }
        },
        "query": {"bool": {"filter": {"term": {"type": "document"}}}},
    }
    response = await client.search(body=body, index=collection_id)
    return [
        bucket["key"] for bucket in response["aggregations"]["document_type"]["buckets"]
    ]


async def delete_opensearch_document(collection_id: str, doc_id: str):
    client = get_client()
    # both the parent and its children have to be searchable for `has_parent` to find anything, and
    # ingesting no longer refreshes as it writes, so a rollback part way through an ingest would
    # otherwise leave behind exactly the vectors it was called to remove
    await client.indices.refresh(index=collection_id)
    child_query = {
        "query": {
            "has_parent": {
                "parent_type": "document",
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"_id": doc_id}},
                        ],
                    }
                },
            }
        }
    }
    await client.delete_by_query(index=collection_id, body=child_query, refresh=True)
    await client.delete(index=collection_id, id=doc_id, refresh=True)
    return 1  # TODO: evaluate what we should actually return here


async def set_temp_file(file_path: str):
    client = get_client()
    if not await client.indices.exists(index=FILES_INDEX_NAME):
        index_body = {"mappings": {"properties": {"file_path": {"type": "keyword"}}}}
        await client.indices.create(index=FILES_INDEX_NAME, body=index_body)
    response = await client.index(
        index=FILES_INDEX_NAME,
        body={"file_path": file_path},
        refresh=True,
    )
    return response["_id"]


async def get_temp_file(id: str):
    client = get_client()
    if await client.indices.exists(index=FILES_INDEX_NAME):
        response = await client.get(index=FILES_INDEX_NAME, id=id)
        return response["_source"]["file_path"]


async def collection_index_exists(collection_id: str) -> bool:
    # the per-collection index is created in both modes, unlike the name mapping, which is only
    # written when security is disabled
    client = get_client()
    return await client.indices.exists(index=collection_id)
