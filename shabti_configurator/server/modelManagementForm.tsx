import {
	ChatModelSelector,
	ModelSelectionFallback,
	resolveModelSelection,
} from "./chatModelSelector";
import { ConnectivityNotice } from "./connectivityNotice";

export const ModelManagementForm = async () => {
	const { connectivity, selection, chatModels, selectedChatModels } =
		await resolveModelSelection({
			fallback: ModelSelectionFallback.AllChatModels,
		});
	return (
		<form action="/manage-models" method="post">
			<fieldset>
				<legend>LLM Configuration</legend>
				<ConnectivityNotice connectivity={connectivity}></ConnectivityNotice>
				<ChatModelSelector
					selectId="manage_language_model"
					containerId="manage_default_model_selector"
					required
					chatModels={chatModels}
					selectedChatModels={selectedChatModels}
					defaultModel={selection.defaultModel}
				></ChatModelSelector>
				<p>
					<label for="current_embeddings_model">Current Embeddings Model</label>
					<select
						name="current_embeddings_model"
						id="current_embeddings_model"
						disabled
					>
						<option value={selection.embeddingsModel} selected>
							{selection.embeddingsModel}
						</option>
					</select>
				</p>
				<p>
					<small>
						You cannot change the embeddings model as this would invalidate all
						currently ingested documents.
					</small>
				</p>
				<button type="submit">Apply Model Changes</button>
			</fieldset>
		</form>
	);
};
