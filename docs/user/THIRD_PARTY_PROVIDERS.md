
# Third-party AI providers

Shabti can also answer prompts with any OpenAI-compatible API, hosted or self-hosted. Each source of models is a provider. The bundled llama.cpp is the provider `local`, and its models keep running on your own hardware.

Every chat model is named `<provider>/<model>`, for example `local/MiniCPM5-2B` or `openrouter/openai/gpt-4o`. Only the first `/` separates the two, so a provider's own model IDs can contain one. The web app groups the models by provider, and the API, the clients and the CLI take and return these names.

## Disclaimer

If you wish to do so, please take note of the information below before proceeding.

We originally designed Shabti as a local model only tool, because we wanted to target users who need to protect their data or even work in an airgapped environment, however we realize that it has applications outside of the self-hosted environment, so we added the possibility to connect other providers. This also means that you can use Shabti if you don't have the hardware to run local models, or even point it at your own local host running a different technology from the one we use (llama.cpp).

The downside, of course, is that you no longer have full control of your data. Any documents ingested into Shabti and any queries will go through your provider's servers, so you must be careful to avoid sensitive data or be sure that you trust the provider to keep it confidential.

You must also check your provider's terms and conditions to see which uses are permitted for your API keys. For example it is common for subscription plans to only allow your account to be used with coding agent type software and for a single user. A multi-user Shabti installation breaks both of those rules.

## How to

Add a provider in the "Model Providers" section of the configurator's install form, or under "Model Providers" in the "Manage Shabti" section. "Add Another Provider" adds several at once. "Test Connection" lists the provider's models the way Shabti will, and you choose which of them to offer. A provider's model can also be the default chat model. If the provider documents an endpoint such as `https://api.example.com/v1/chat/completions` rather than its base URL, paste it in as it is: the configurator removes the endpoint path.

The configurator records providers in `docker_compose/docker_compose_dependencies/shabti_config/shabti-providers.json`, which you can also edit by hand:

```json
{
	"providers": [
		{
			"id": "openrouter",
			"name": "OpenRouter",
			"base_url": "https://openrouter.ai/api/v1",
			"headers": { "X-Title": "Shabti" },
			"models": {
				"allow": ["openai/gpt-4o"],
				"overrides": { "openai/gpt-4o": { "context_length": 128000, "label": "GPT-4o" } }
			}
		}
	],
	"default_chat_model": "openrouter/openai/gpt-4o"
}
```

- `id` is lowercase letters, digits and `-`. `local` is reserved.
- `base_url` includes the API version, usually ending in `/v1`.
- `allow` limits which of the provider's models are offered. Leave it out to offer them all, or leave it empty to offer none, for a provider that only embeds.
- `overrides` correct what Shabti works out about a model: its `category` (`chat` or `embeddings`), `context_length` and `label`.
- `credential_mode` is whose key requests are made with: `shared` (the default) for the key below, `user` for each user's own, or `shared_or_user` for the user's own when they've added one and otherwise the shared key. Users' own keys need security to be enabled, see [Provider API Keys](Security_and_RBAC_Features.md#provider-api-keys). The provider of the embeddings model always uses the shared key.
- `default_chat_model` is the model a user starts on before they choose one.

Shabti looks up each remote model's context length on [models.dev](https://models.dev), in a copy the configurator downloads each time it starts. A model it can't find there is assumed to take 8k tokens, unless the provider's model list or `overrides` say otherwise.

### Reasoning

A model that can be told how much to think gets a "Reasoning" choice in the prompter. Its values are the provider's own, such as `low`, `high` or `none`, or Off and On for a model that can only be switched. Shabti finds them in the provider's model list where it says (OpenRouter, Anthropic), and otherwise on models.dev. A local model's are read from its chat template once it is loaded.

- `overrides` can set a model's `reasoning`, as a list such as `["low", "medium", "high"]` or `[false, true]`, and `reasoning_default`. An empty list offers no choice.
- `reasoning_format` is how the provider is told. Shabti works it out from the base URL for OpenRouter (`openrouter`), Anthropic (`anthropic`), DeepSeek, Moonshot and Z.ai (`thinking`) and Alibaba DashScope (`enable_thinking`), and uses `reasoning_effort` (`effort`) for anything else. Set it for another provider that needs one of these, or to `llama_cpp` for a llama.cpp server.
- For a local model, set `shabti_reasoning` in its catalogue entry, e.g. `shabti_reasoning = ["low", "high"]`.

Anthropic and OpenAI follow the choice but don't send back what the model thought.

API keys are kept apart from this file, in `docker_compose/docker_compose_dependencies/secrets/shabti_provider_keys.json`, as `{"openrouter": "sk-..."}`. That directory is mounted into the API container alone. A provider without a key is sent none, which suits most self-hosted servers.

Changes to either file are picked up without restarting Shabti.

> [!NOTE]
> With security enabled, each request to a provider carries a hash of the user's ID, salted per installation, so the provider can tell one user's traffic from another's without learning who they are. Everything a prompt includes, the matched document extracts among it, is sent to the provider.

## Embeddings from a provider

A provider can also embed your documents, instead of a local embeddings model. Choose "Model Provider" as the embeddings source when installing, test the connection to the provider, and search its models for the embeddings one. Like a local one, it can't be changed after installing.

The models a provider says embed are listed first, including those it lists apart from its others, as OpenRouter does. Any of its other models can be chosen too. A provider added only to embed with doesn't need any of its chat models chosen.

- **Dimensions** asks a model that can shorten its vectors for that many. Leave it empty for the model's own size.
- **Chunk size** is how many of the model's tokens a document chunk may take up. It defaults to 512.
- **Tokenizer** is how chunks are measured. "Estimate" counts characters, which suits any model. Pick a tiktoken encoding for OpenAI's models, or "Hugging Face" and the model's repository to download its own `tokenizer.json` at install.
- **Batch size** is how many chunks go in one request. It defaults to 64.

The provider's model is recorded under `embeddings` in `shabti-providers.json`, and its settings in `docker_compose/docker_compose_dependencies/llama_models/shabti-models.json`. That file also takes `context_limit`, `max_concurrency` (4 requests at once by default) and `extra_body`, extra fields sent with every request:

```json
{
	"openai/text-embedding-3-small": {
		"chunk_size": 512,
		"tokenizer": "tiktoken:cl100k_base",
		"batch_size": 64,
		"max_concurrency": 2
	}
}
```

A provider that is rate limiting is retried, waiting as long as it asks. The embeddings provider can't be removed while Shabti uses it.

When every model comes from a provider, Shabti is installed without llama.cpp. Adding a local chat model under "Manage Shabti" brings it back, and removing the last one leaves it out again.

> [!CAUTION]
> Every document you ingest is sent to the embeddings provider.

### When the embeddings model changes

Nothing guarantees that a model ID keeps producing the same vectors. A provider can retire a model or move an alias to a newer one, and a local model can change when it is downloaded again. Vectors from before and after aren't comparable, so retrieval would quietly get worse.

So Shabti embeds a few fixed texts when the first collection is created and keeps the vectors. It embeds them again each time it starts and at most hourly after that. If they no longer match, the status page shows the embeddings as unavailable, and ingesting and prompting fail with "The embeddings model has changed; documents must be re-ingested." Delete every collection and ingest the documents again to start over with the model as it is now. Where a provider offers a pinned model version, pick that one.

