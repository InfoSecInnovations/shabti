# Shabti default model list

Shabti can be configured to use any model which can be run by llama.cpp on your hardware. We have however included a list of recommended models to try which will show in the configurator at install time, if you wish to use models not on this list, keep reading.

> [!CAUTION]
> We have not tried out all of these models due to time and hardware constraints, so while we have tried our best to provide a convenient selection, please independently evaluate any models you wish to use beforehand and use at your own risk.

To see the full list of models included with Shabti, look at [shabti_models.ini](/shabti_configurator/shabti_models.ini), you can also use this as an example when setting up your custom models.

## Our selection process

It's difficult to precisely evaluate every single model without spending considerable time and effort using each one, so we have included some of the highest scoring models for each size category on intelligence benchmarks, while bearing in mind that said benchmarks are not a 100% accurate of how "good" a model is. We have also made sure to cover a range of providers. If there's a model you think we left off which deserves a spot in the defaults, let us know!

We used 4 bit quantizations of each chat model as this provides the best balance between hardware requirements and model response quality. The rough rule of thumb is that an 8 bit quantization requires 1GB of VRAM per Billion parameters, and a 4 bit quantization uses half of that. There is some additional overhead which is difficult to evaluate precisely, so you should pick models which will use up less than your total VRAM, and if you run into issues, use smaller models. Bear in mind that Shabti requires both the embeddings model and at least one chat model to function, so you need sufficient hardware to host both at the same time. You may be surprised at how well Shabti works with smaller models, as the document collection system does a lot of heavy lifting!

For embeddings we used 8 bit quantizations as these are already very small models for the most part, and we want high precision in the retrieval results. You may also use 8 bit versions for chat models if you have the hardware to do so, see below for information on custom model configuration.

## Custom model listing

Create a `custom_models.ini` in the same directory as the configurator executable. Here is how the formatting works:

```
[embeddinggemma-300M] # model ID
hf = ggml-org/embeddinggemma-300M-GGUF:Q8_0 # Hugging Face repo and quantization
tags = embeddings, default # embeddings or chat, if you add default here, this model will be the default in the configurator options
shabti_query_prefix = "task: search result | query: " # only include this for embeddings models which require a prefix when querying
shabti_document_prefix = "title: none | text: " # only include this for embeddings models which require a prefix when ingesting
```

After saving your changes and rerunning the configurator, you should see your models in the lists.

If you use the same model ID as a model already in the Shabti defaults, you can override the configuration this way. 