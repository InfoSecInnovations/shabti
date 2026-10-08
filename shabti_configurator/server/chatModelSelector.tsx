import getDefaultModelSelection from "../getDefaultModelSelection";
import getAvailableModels from "./getAvailableModels";
import readModelsIni from "./readModelsIni";

const collator = new Intl.Collator("en");

// resolves the models which are currently configured, falling back to the catalogue's default
// model when Shabti hasn't been installed yet. Only the models which can be used right now are
// offered, which offline means the downloaded ones
export const resolveModelSelection = async () => {
	const { models: shabtiModels, connectivity } = await getAvailableModels();
	const configured = await readModelsIni();
	const defaults = await getDefaultModelSelection();
	const selection = configured || {
		...defaults,
		chatModels: defaults.defaultModel ? [defaults.defaultModel] : [],
	};
	const modelsTagged = (tag: string) =>
		Object.entries(shabtiModels)
			.filter(([_, v]) => v.tags.includes(tag))
			.map(([k]) => k)
			.sort(collator.compare);
	const chatModels = modelsTagged("chat");
	const embeddingsModels = modelsTagged("embeddings");
	// filter the catalogue rather than the selection so the option order stays stable
	const selectedChatModels = chatModels.filter((model) =>
		selection.chatModels.includes(model),
	);
	// the preselected models may not be downloaded, so something else is selected in their place,
	// unless there were none to begin with, which leaves Shabti only able to search
	if (
		!selectedChatModels.length &&
		selection.chatModels.length &&
		chatModels.length
	)
		selectedChatModels.push(chatModels[0]!);
	return {
		shabtiModels,
		connectivity,
		selection,
		chatModels,
		embeddingsModels,
		selectedChatModels,
	};
};

// the default chat model can only be chosen when more than one model is selected, so the
// selector lives in its own container which the client patches as the selection changes.
// The ids are parameters because both the install form and the model management form have one
// and they have to be unique across the page, and the container's inner select is always
// `${containerId}_select`, which wireDefaultModelSelector in the client bundle relies on, as it
// does on `${containerId}_no_chat_model` for the note shown when nothing is selected.
export const ChatModelSelector = (props: {
	selectId: string;
	containerId: string;
	chatModels: string[];
	selectedChatModels: string[];
	defaultModel?: string;
}) => (
	<>
		<p>
			<label for={props.selectId}>Select Chat Models</label>
			<select name="language_model" id={props.selectId} multiple>
				{props.chatModels.map((model) => (
					<option
						value={model}
						selected={props.selectedChatModels.includes(model)}
					>
						{model}
					</option>
				))}
			</select>
		</p>
		<p>
			<small>
				The language models which will be available to users when querying
				Shabti.
			</small>
		</p>
		<p
			id={`${props.containerId}_no_chat_model`}
			class={props.selectedChatModels.length ? "hidden" : undefined}
		>
			<small>
				With no chat model, Shabti can only retrieve documents, effectively
				functioning as a search engine on your documents.
			</small>
		</p>
		<div id={props.containerId}>
			{props.selectedChatModels.length > 1 && (
				<p>
					<label for={`${props.containerId}_select`}>Default Chat Model</label>
					<select id={`${props.containerId}_select`} name="default_model">
						{props.selectedChatModels.map((model) => (
							<option value={model} selected={model == props.defaultModel}>
								{model}
							</option>
						))}
					</select>
				</p>
			)}
		</div>
	</>
);
