import getDefaultModelSelection from "../getDefaultModelSelection";
import getAvailableModels from "./getAvailableModels";
import readModelsIni from "./readModelsIni";

// which models to preselect when there's no my-models.ini to read
export enum ModelSelectionFallback {
	AllChatModels = "all-chat-models",
	DefaultModelOnly = "default-model-only",
}

// resolves the models which are currently configured, falling back to the bundled catalogue
// when Shabti hasn't been installed yet. Only the models which can be used right now are
// offered, which offline means the downloaded ones
export const resolveModelSelection = async (options: {
	fallback: ModelSelectionFallback;
}) => {
	const { models: shabtiModels, connectivity } = await getAvailableModels();
	const configured = await readModelsIni();
	const defaults = await getDefaultModelSelection();
	const selection =
		configured ||
		(options.fallback == ModelSelectionFallback.DefaultModelOnly
			? { ...defaults, chatModels: [defaults.defaultModel] }
			: defaults);
	const modelsTagged = (tag: string) =>
		Object.entries(shabtiModels)
			.filter(([_, v]) => v.tags.includes(tag))
			.map(([k]) => k);
	const chatModels = modelsTagged("chat");
	const embeddingsModels = modelsTagged("embeddings");
	// filter the catalogue rather than the selection so the option order stays stable
	const selectedChatModels = chatModels.filter((model) =>
		selection.chatModels.includes(model),
	);
	// the preselected models may not be downloaded, but something has to be selected
	if (!selectedChatModels.length && chatModels.length)
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
// `${containerId}_select`, which wireDefaultModelSelector in the client bundle relies on.
export const ChatModelSelector = (props: {
	selectId: string;
	containerId: string;
	required?: boolean;
	chatModels: string[];
	selectedChatModels: string[];
	defaultModel: string;
}) => (
	<>
		<p>
			<label for={props.selectId}>Select Chat Models</label>
			<select
				name="language_model"
				id={props.selectId}
				multiple
				required={props.required}
			>
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
