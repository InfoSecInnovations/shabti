import getModelsConfig from "./getModelsConfig";

// what the API needs to know about a provider's embeddings model, which nothing can discover
export interface RemoteEmbeddingsSettings {
	chunk_size: number;
	// estimate, tiktoken:<encoding> or hf:<repo>
	tokenizer: string;
	batch_size?: number;
}

export interface RemoteEmbeddings {
	// qualified, as <provider>/<model>
	model: string;
	dimensions?: number;
	settings: RemoteEmbeddingsSettings;
}

export interface ModelSelection {
	chatModels: string[];
	// the local embeddings model, absent when the embeddings come from a provider
	embeddingsModel?: string;
	remoteEmbeddings?: RemoteEmbeddings;
	// undefined when there are no chat models, which leaves Shabti only able to search
	defaultModel?: string;
}

// the models llama.cpp has to serve for this selection
export const localModels = (selection: ModelSelection) => [
	...selection.chatModels,
	...(selection.embeddingsModel && !selection.remoteEmbeddings
		? [selection.embeddingsModel]
		: []),
];

// derives a usable model selection straight from the bundled catalogue,
// used by the test harness and as a fallback for anything the install form didn't set
export default async (): Promise<
	ModelSelection & { embeddingsModel: string }
> => {
	const shabtiModels = await getModelsConfig();
	const entries = Object.entries(shabtiModels);
	const chatModels = entries
		.filter(([_, v]) => v.tags.includes("chat"))
		.map(([k]) => k);
	const embeddingsModel = entries.find(([_, v]) =>
		v.tags.includes("embeddings"),
	)?.[0];
	if (!embeddingsModel)
		throw new Error("no embeddings model is available in the models config");
	return {
		chatModels,
		embeddingsModel,
		defaultModel:
			chatModels.find((k) => shabtiModels[k].tags.includes("default")) ||
			chatModels[0],
	};
};
