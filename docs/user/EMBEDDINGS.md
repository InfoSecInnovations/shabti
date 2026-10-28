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

| Model | Chunk | SciFact en | SciFact es | SciFact ja | NFCorpus en | NFCorpus es | NFCorpus ja | Mean | Empty |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all-MiniLM-L6-v2 | 128 | 0.815 | 0.602 | 0.446 | 0.290 | 0.258 | 0.154 | 0.427 | 1.7% |
| all-MiniLM-L6-v2 | 256 | 0.777 | 0.622 | 0.449 | 0.307 | 0.257 | 0.148 | 0.427 | 2.0% |
| all-MiniLM-L6-v2 | 512 | 0.797 | 0.603 | 0.455 | 0.336 | 0.251 | 0.166 | 0.435 | 3.0% |
| bge-m3 | 128 | 0.725 | 0.724 | 0.712 | 0.309 | 0.265 | 0.274 | 0.502 | 0.0% |
| bge-m3 | 256 | 0.740 | 0.726 | 0.730 | 0.340 | 0.293 | 0.300 | 0.521 | 0.0% |
| bge-m3 | 512 | 0.738 | 0.703 | 0.733 | 0.364 | 0.315 | 0.316 | 0.528 | 0.0% |
| nomic-embed-text-v2-moe | 128 | 0.743 | 0.743 | 0.695 | 0.280 | 0.288 | 0.257 | 0.501 | 2.0% |
| nomic-embed-text-v2-moe | 256 | 0.759 | 0.748 | 0.724 | 0.311 | 0.278 | 0.284 | 0.517 | 2.0% |
| nomic-embed-text-v2-moe | 512 | 0.779 | 0.747 | 0.742 | 0.343 | 0.296 | 0.299 | 0.534 | 2.0% |
| embeddinggemma-300M | 128 | 0.814 | 0.760 | 0.711 | 0.304 | 0.259 | 0.287 | 0.522 | 1.7% |
| embeddinggemma-300M | 256 | 0.827 | 0.783 | 0.764 | 0.356 | 0.282 | 0.315 | 0.555 | 1.7% |
| embeddinggemma-300M | 512 | 0.826 | 0.793 | 0.777 | 0.388 | 0.323 | 0.320 | 0.571 | 1.7% |
| snowflake-arctic-embed-s | 128 | 0.749 | 0.616 | 0.413 | 0.310 | 0.270 | 0.151 | 0.418 | 0.0% |
| snowflake-arctic-embed-s | 256 | 0.750 | 0.668 | 0.489 | 0.330 | 0.279 | 0.176 | 0.449 | 0.0% |
| snowflake-arctic-embed-s | 512 | 0.762 | 0.702 | 0.512 | 0.351 | 0.307 | 0.151 | 0.464 | 0.0% |
| snowflake-arctic-embed-l-v2.0 | 128 | 0.749 | 0.743 | 0.709 | 0.276 | 0.239 | 0.274 | 0.498 | 2.3% |
| snowflake-arctic-embed-l-v2.0 | 256 | 0.763 | 0.745 | 0.732 | 0.324 | 0.289 | 0.290 | 0.524 | 2.7% |
| snowflake-arctic-embed-l-v2.0 | 512 | 0.766 | 0.742 | 0.739 | 0.329 | 0.323 | 0.310 | 0.535 | 2.3% |

Empty is the share of answerable questions that got no references.

Caveats:

- The datasets are the NanoBEIR versions of SciFact (scientific claims) and NFCorpus (nutrition and medical questions), with 50 queries and about 2,900 documents each. They come from `LiquidAI/nanobeir-multilingual-extended` on Hugging Face and are downloaded at run time.
- Nano scores run higher than full-dataset scores because the corpus is smaller.
- 50 queries is noisy. A difference under about 0.05 is usually not significant.
- The Spanish and Japanese sets are machine-translated.
- NFCorpus loses its graded relevance in the nano version, so every listed match counts the same.

### Recommendations

- The current default is `all-MiniLM-L6-v2` at chunk size 128 (Mean 0.427). It is weak outside English: its Japanese scores at 128 (0.446 on SciFact, 0.154 on NFCorpus) are well below the multilingual models.
- The best configuration in the table is `embeddinggemma-300M` at 512 (Mean 0.571).
- The table cannot show where hybrid search beats vector search alone, since every run uses hybrid search.

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

- Five of the six models score higher at 512 than at 128 (Mean): `embeddinggemma-300M` 0.522 → 0.571, `snowflake-arctic-embed-l-v2.0` 0.498 → 0.535, `nomic-embed-text-v2-moe` 0.501 → 0.534, `bge-m3` 0.502 → 0.528, `snowflake-arctic-embed-s` 0.418 → 0.464.
- `all-MiniLM-L6-v2` barely changes (Means 0.427 / 0.427 / 0.435 at 128 / 256 / 512), and its Empty rises from 1.7% to 3.0%.
- Larger chunks also mean longer references, so a gain should be weighed against that.
- Differences under about 0.05 on a single dataset are within noise (see the caveats above).

## Multilingual behaviour

- `all-MiniLM-L6-v2` and `snowflake-arctic-embed-s` drop sharply in Japanese. On SciFact at 128, `all-MiniLM-L6-v2` scores 0.815 in English and 0.446 in Japanese, and `snowflake-arctic-embed-s` scores 0.749 and 0.413.
- `embeddinggemma-300M` at 512 has the best Mean (0.571). It is the best on all six datasets at 512, and tied with `snowflake-arctic-embed-l-v2.0` on NFCorpus Spanish (0.323).
- `bge-m3` has the flattest scores across languages on SciFact: 0.725 in English, 0.724 in Spanish and 0.712 in Japanese at 128.
- `all-MiniLM-L6-v2` is English-only in practice.
- OpenSearch's standard analyser is weak at keyword search in Japanese and Chinese, so hybrid search helps less for those languages.

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
