# Embeddings models

Shabti uses an embeddings model to find the parts of your documents that answer a question. This page covers the embeddings models Shabti ships, how search combines them with keyword matching, how chunk size changes results, how the models handle other languages, and how to benchmark a model of your own with `bun run bench`. For chat models and the general custom model format, see [MODELS.md](MODELS.md).

## Shipped models

- `all-MiniLM-L6-v2`: the default. English-only in practice.
- `bge-m3`: multilingual.
- `nomic-embed-text-v2-moe`: multilingual.
- `embeddinggemma-300M`: multilingual.
- `snowflake-arctic-embed-s`
- `snowflake-arctic-embed-l-v2.0`: multilingual.

The models are defined in [`shabti_models.ini`](../../shabti_configurator/shabti_models.ini), in the sections tagged `embeddings`.

## Benchmark results

The benchmark runs every shipped model at chunk sizes 128, 256 and 512, on the nano versions of SciFact and NFCorpus in English, Spanish and Japanese, with hybrid search as shipped (see [How hybrid search works](#how-hybrid-search-works)).

<!-- results -->

Caveats:

- The datasets are the NanoBEIR versions of SciFact (scientific claims) and NFCorpus (nutrition and medical questions), with 50 queries and about 2,900 documents each. They come from `LiquidAI/nanobeir-multilingual-extended` on Hugging Face and are downloaded at run time.
- Nano scores run higher than full-dataset scores because the corpus is smaller.
- 50 queries is noisy. A difference under about 0.05 is usually not significant.
- The Spanish and Japanese sets are machine-translated.
- NFCorpus loses its graded relevance in the nano version, so every listed match counts the same.

## What the scores mean

- **nDCG@10** is the main score, from 0 to 1. Published leaderboards such as MTEB show it multiplied by 100, so 0.75 here is 75 there. It rewards putting the relevant documents near the top of the 10 references.
- **Hit@5** is the share of questions with at least one relevant document in the top 5.
- **Recall@5** is the share of all relevant documents found in the top 5.
- **MRR@10** measures how high the first relevant document appears.
- **Empty** is the share of answerable questions that got no references at all.

The summary table shows nDCG@10 for each dataset, their mean, and Empty. Each run's `report.md` has all of the metrics.

## How hybrid search works

Every search runs two searches side by side in one request:

- a vector (kNN) search on the embeddings;
- a keyword (BM25) search on the chunk text.

Each search's scores are scaled to 0 to 1 (min-max), then averaged 50/50. Keyword search finds exact terms, names, product codes and identifiers, which embeddings tend to blur.

A chunk must reach a vector similarity of at least 0.6 (`MIN_SCORE`) to count. If the vector search finds nothing above that, the answer has no references at all, even when keywords matched. The similarity floor is the only signal that nothing is relevant.

- The keyword search uses only the first 1000 characters of a very long question.
- There are no settings for any of this. Hybrid search is always on.

This fusion was chosen because, across the six shipped embeddings models on the full SciFact and NFCorpus datasets, 50/50 min-max fusion had the best mean score of the variants tried. It was not significantly worse than vector search alone in any case.

## Chunk size

A chunk is the passage that is embedded and shown as a reference. The chunk size therefore sets both what the model sees and how long each reference is.

Set it per model with `shabti_chunk_size` in the model's section, in `shabti_models.ini` or your `custom_models.ini`:

```ini
; a section with a shipped model's name replaces that section,
; so keep its other settings
[embeddinggemma-300M]
hf = ggml-org/embeddinggemma-300M-GGUF:Q8_0
tags = embeddings
shabti_query_prefix = "task: search result | query: "
shabti_document_prefix = "title: none | text: "
shabti_chunk_size = 256
```

- The default is 128 tokens when the setting is absent.
- Shabti refuses a chunk size larger than the model's trained context.
- The installer sizes llama.cpp's batch to the chunk size plus 64 tokens.

What we observed:

- Small and English-only models do best at small chunks.
- Larger multilingual models (embeddinggemma, bge-m3) tend to gain at 256 to 512.
- A model can return empty answers more often at larger chunk sizes, because of the single 0.6 floor (see [Known limits](#known-limits)).

<!-- chunk-size findings -->

## Multilingual behaviour

- `all-MiniLM-L6-v2` is English-only in practice.
- `embeddinggemma-300M` was the strongest in every language tested.
- `bge-m3` was the most consistent across languages, but scores lower overall.
- OpenSearch's standard analyser is weak at keyword search in Japanese and Chinese, so hybrid search helps less for those languages.

<!-- multilingual findings -->

## Trying a candidate model

### Prerequisites

- A clone of the repo.
- [bun](https://bun.sh).
- Docker.
- An NVIDIA GPU, which is the default (`--compute cuda`). `--compute cpu` works without one, but is much slower.

### Model definition

Add the candidate as a section in `custom_models.ini` at the repo root, in the same format [MODELS.md](MODELS.md) shows, with `tags = embeddings`:

```ini
[my-model]
hf = your-org/your-model-GGUF:Q8_0
tags = embeddings
; optional, add any prefixes the model needs
; shabti_query_prefix = "..."
; shabti_document_prefix = "..."
; optional, tokens per chunk (default 128)
shabti_chunk_size = 256
```

A section with the same name as a shipped model replaces it. Use this to try different prefixes, or a different quantisation of a shipped model.

### Commands

```
# all three chunk sizes, six nano datasets
bun run bench --models my-model

# compare against the default
bun run bench --models my-model all-MiniLM-L6-v2 --chunk-sizes 256

# your own data
bun run bench --datasets ./my-dataset
```

Run `bun run bench --help` for every option.

### Custom datasets

A custom dataset is a folder in the BEIR format:

- `corpus.jsonl`: one JSON object per line. The `title` is optional.

  ```json
  {"_id": "d1", "title": "Optional title", "text": "The passage text."}
  ```

- `queries.jsonl`: one JSON object per line.

  ```json
  {"_id": "q1", "text": "The question?"}
  ```

- `qrels/test.tsv`: tab-separated, with a header row. A score above 0 means relevant.

  ```
  query-id	corpus-id	score
  q1	d1	1
  ```

Only queries listed in `qrels/test.tsv` are scored.

### Caching

- Results are cached per model, chunk size and dataset in `testing/bench_results/runs/`. A rerun reuses them.
- Each model and chunk size keeps its search index, which a rerun also reuses.
- `--rerun` ignores cached results. `--fresh` also rebuilds the index.
- Changing a model's section in the INI rebuilds its index automatically.

### Runtime

A run takes roughly 10 minutes per model, chunk size and dataset on a mid-range GPU, mostly spent indexing. The full default matrix is 108 runs. The test stack can't be up at the same time as the bench, and the bench takes it down first.

### Results

The summary table is printed at the end and saved to `testing/bench_results/summary.md`. Each run's full metrics are in its `report.md`.

### Sharing a configuration

If a configuration works well, open an issue or pull request on the Shabti GitHub repository with:

- the model's INI section;
- the summary table;
- your GPU.

A configuration that does better than a shipped one is a candidate for the catalogue.

## Known limits

`MIN_SCORE = 0.6` is one cosine floor for every embeddings model and every chunk size. Score distributions differ between models, so some model and chunk size combinations return no references for questions that do have an answer. The Empty column in the results shows how often this happens.
