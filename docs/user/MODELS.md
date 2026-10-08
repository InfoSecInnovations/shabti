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