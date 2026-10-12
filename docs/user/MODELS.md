# Shabti default model list

Shabti can be configured to use any model which can be run by llama.cpp on your hardware. We have however included a list of recommended models to try which will show in the configurator at install time, if you wish to use models not on this list, keep reading.

> [!CAUTION]
> We have not tried out all of these models due to time and hardware constraints, so while we have tried our best to provide a convenient selection, please independently evaluate any models you wish to use beforehand and use at your own risk.

To see the full list of models included with Shabti, look at [shabti_models.ini](/shabti_configurator/shabti_models.ini), you can also use this as an example when setting up your custom models.

## Our selection process

It's difficult to precisely evaluate every single model without spending considerable time and effort using each one, so we have included some of the highest scoring models for each size category on intelligence benchmarks, while bearing in mind that said benchmarks are not a 100% accurate representation of how "good" a model is. We have also made sure to cover a range of providers. If there's a model we left off which you think deserves a spot in the defaults, let us know!

We used 4 bit quantizations of each chat model as this provides the best balance between hardware requirements and model response quality. The rough rule of thumb is that an 8 bit quantization requires slightly over 1GB of VRAM per Billion parameters, and a 4 bit quantization uses half of that. You can see the exact file sizes by browsing the Hugging Face repository containing the model.

Bear in mind that Shabti needs to host the embeddings model and a chat model at the same time, so you need sufficient hardware for both. The chat model is optional: with no chat model, Shabti can only retrieve documents, effectively functioning as a search engine on your documents. You may be surprised at how well Shabti works with smaller models, as the document collection system does a lot of heavy lifting!

For embeddings we used 8 bit quantizations as these are already very small models for the most part, and we want high precision in the retrieval results. You may also use 8 bit versions for chat models if you have the hardware to do so, see below for information on custom model configuration.

## Custom model listing

Create a `custom_models.ini` in the same directory as the configurator executable. Here is how the formatting works:

```ini
; model ID
[embeddinggemma-300M] 
; Hugging Face repo and quantization
hf = ggml-org/embeddinggemma-300M-GGUF:Q8_0 
; embeddings or chat, if you add default here, this model will be the default in the configurator options
tags = embeddings, default 
; only include this for embeddings models which require a prefix when querying
shabti_query_prefix = "task: search result | query: " 
; only include this for embeddings models which require a prefix when ingesting
shabti_document_prefix = "title: none | text: " 
```

The prefix requirements are very variable for each embeddings model, some of them don't use prefixes at all, some only on the query or document. Some such as Gemma above can use custom prompts to match the purpose of the embedding more exactly. In future editions of Shabti we aim to experiment with letting different Shabti tasks insert a custom prompt for models which allow it. 

After saving your changes and rerunning the configurator, you should see your models in the lists.

If you use the same model ID as a model already in the Shabti defaults, you can override the configuration this way. 
## Providers

Shabti can also answer prompts with any OpenAI-compatible API, hosted or self-hosted. Each source of models is a provider. The bundled llama.cpp is the provider `local`, and its models keep running on your own hardware.

Every chat model is named `<provider>/<model>`, for example `local/MiniCPM5-2B` or `openrouter/openai/gpt-4o`. Only the first `/` separates the two, so a provider's own model IDs can contain one. The web app groups the models by provider, and the API, the clients and the CLI take and return these names.

Add a provider in the "Model Provider" section of the configurator's install form, or under "Model Providers" in the "Manage Shabti" section. "Test Connection" lists the provider's models the way Shabti will, and you choose which of them to offer. A provider's model can also be the default chat model. If the provider documents an endpoint such as `https://api.example.com/v1/chat/completions` rather than its base URL, paste it in as it is: the configurator removes the endpoint path.

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
- `allow` limits which of the provider's models are offered. Leave it out to offer them all.
- `overrides` correct what Shabti works out about a model: its `category` (`chat` or `embeddings`), `context_length` and `label`.
- `default_chat_model` is the model a user starts on before they choose one.

Shabti looks up each remote model's context length on [models.dev](https://models.dev), in a copy the configurator downloads each time it starts. A model it can't find there is assumed to take 8k tokens, unless the provider's model list or `overrides` say otherwise.

API keys are kept apart from this file, in `docker_compose/docker_compose_dependencies/secrets/shabti_provider_keys.json`, as `{"openrouter": "sk-..."}`. That directory is mounted into the API container alone. A provider without a key is sent none, which suits most self-hosted servers.

Changes to either file are picked up without restarting Shabti.

> [!NOTE]
> With security enabled, each request to a provider carries a hash of the user's ID, salted per installation, so the provider can tell one user's traffic from another's without learning who they are. Everything a prompt includes, the matched document extracts among it, is sent to the provider.
