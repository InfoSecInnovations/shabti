import getEnvs from "./getEnvs";
import { HostsAndPortsFieldset } from "./hostsAndPortsFieldset";
import { LoggingFieldset } from "./loggingFieldset";
import { VersionSelector } from "./versionSelector";
import currentIsLocal from "./currentIsLocal";
import listCompatibleVersions from "./listCompatibleVersions";
import { ConnectivityNotice } from "./connectivityNotice";
import { ChatModelSelector, resolveModelSelection } from "./chatModelSelector";
import { ProviderList } from "./providerFieldset";
import {
	DEFAULT_REMOTE_BATCH_SIZE,
	DEFAULT_REMOTE_CHUNK_SIZE,
	remoteModels,
	tokenizerRepo,
} from "./providers";
import { readProviders } from "./writeProviders";
import { readShabtiModels } from "./writeModelsIni";
import type { RemoteEmbeddingsSettings } from "../getDefaultModelSelection";

const TOKENIZER_OPTIONS = {
	estimate: "Estimate",
	"tiktoken:cl100k_base": "tiktoken cl100k_base",
	"tiktoken:o200k_base": "tiktoken o200k_base",
	hf: "Hugging Face",
};

export const InstallOptionsForm = async (props: {
	devMode: boolean;
	currentVersion?: string;
}) => {
	const envs = await getEnvs();
	const securityEnabled = envs.SHABTI_SECURITY_ENABLED == "True";
	const demoEnabled = securityEnabled && envs.IS_SECURITY_DEMO == "True";
	const gpuEnabled = envs.SHABTI_COMPUTE == "cuda";
	const {
		connectivity,
		selection,
		chatModels,
		embeddingsModels,
		selectedChatModels,
	} = await resolveModelSelection();
	const versions = await listCompatibleVersions();
	// a chat model is optional, without one Shabti can still search
	if ((!versions.length && !props.devMode) || !embeddingsModels.length)
		return <p class="error">You need to be online to install Shabti.</p>;
	// the installed providers fill the form's provider fieldsets in order, so a reinstall keeps them
	const { config, keys } = await readProviders();
	const embeddingsIndex = config.providers.findIndex((provider) =>
		config.embeddings?.model.startsWith(`${provider.id}/`),
	);
	const embeddingsProvider = config.providers[embeddingsIndex];
	const remoteEmbeddings =
		config.embeddings && embeddingsProvider
			? {
					// named after the fieldset it's chosen from, as the client names it
					value: `install_${embeddingsIndex}_:${config.embeddings.model.slice(embeddingsProvider.id.length + 1)}`,
					dimensions: config.embeddings.dimensions,
					settings: ((await readShabtiModels())[config.embeddings.model] ??
						{}) as Partial<RemoteEmbeddingsSettings>,
				}
			: undefined;
	const tokenizer = remoteEmbeddings?.settings.tokenizer || "estimate";
	const repo = tokenizerRepo(tokenizer);
	return (
		<form action="/install" method="post" id="install_form">
			<ConnectivityNotice connectivity={connectivity}></ConnectivityNotice>
			<fieldset>
				<legend>Version</legend>
				<p>
					<VersionSelector
						id="install_version"
						devMode={props.devMode}
						currentVersion={
							props.devMode && (await currentIsLocal())
								? "local"
								: props.currentVersion
						}
					></VersionSelector>
				</p>
			</fieldset>
			<fieldset>
				<legend>LLM Configuration</legend>
				<p>
					<input
						type="checkbox"
						id="use_gpu"
						name="use_gpu"
						checked={gpuEnabled}
					></input>
					<label for="use_gpu">Enable GPU Acceleration</label>
				</p>
				<ChatModelSelector
					selectId="language_model"
					containerId="default_model_selector"
					chatModels={chatModels}
					selectedChatModels={selectedChatModels}
					addedModels={remoteModels(config)}
					defaultModel={config.default_chat_model ?? selection.defaultModel}
				></ChatModelSelector>
				<p>
					<label for="embeddings_source">Embeddings Source</label>
					<select name="embeddings_source" id="embeddings_source">
						<option value="local" selected={!remoteEmbeddings}>
							Local
						</option>
						<option value="provider" selected={!!remoteEmbeddings}>
							Model Provider
						</option>
					</select>
				</p>
				<p
					id="local_embeddings"
					class={remoteEmbeddings ? "hidden" : undefined}
				>
					<label for="embeddings_model">Select Embeddings Model</label>
					<select name="embeddings_model" id="embeddings_model">
						{embeddingsModels.map((k) => (
							<option value={k} selected={k == selection.embeddingsModel}>
								{k}
							</option>
						))}
					</select>
				</p>
				<div
					id="remote_embeddings"
					class={remoteEmbeddings ? undefined : "hidden"}
				>
					<p>
						<label for="remote_embeddings_model">Embeddings Model</label>
						<select name="remote_embeddings_model" id="remote_embeddings_model">
							{remoteEmbeddings ? (
								<option value={remoteEmbeddings.value} selected>
									{remoteEmbeddings.value.slice(
										remoteEmbeddings.value.indexOf(":") + 1,
									)}
								</option>
							) : undefined}
						</select>
					</p>
					<p>
						<small>
							Test the connection to the model provider, then search its models.
						</small>
					</p>
					<p>
						<label for="embeddings_dimensions">Dimensions</label>
						<input
							type="number"
							min="1"
							id="embeddings_dimensions"
							name="embeddings_dimensions"
							placeholder="the model's own"
							value={
								remoteEmbeddings?.dimensions
									? String(remoteEmbeddings.dimensions)
									: undefined
							}
						></input>
					</p>
					<p>
						<label for="embeddings_chunk_size">Chunk Size</label>
						<input
							type="number"
							min="1"
							id="embeddings_chunk_size"
							name="embeddings_chunk_size"
							value={String(
								remoteEmbeddings?.settings.chunk_size ??
									DEFAULT_REMOTE_CHUNK_SIZE,
							)}
						></input>
					</p>
					<p>
						<label for="embeddings_tokenizer">Tokenizer</label>
						<select name="embeddings_tokenizer" id="embeddings_tokenizer">
							{Object.entries(TOKENIZER_OPTIONS).map(([value, label]) => (
								<option
									value={value}
									selected={value == (repo ? "hf" : tokenizer)}
								>
									{label}
								</option>
							))}
						</select>
					</p>
					<p
						id="embeddings_tokenizer_repo_container"
						class={repo ? undefined : "hidden"}
					>
						<label for="embeddings_tokenizer_repo">
							Hugging Face Repository
						</label>
						<input
							type="text"
							id="embeddings_tokenizer_repo"
							name="embeddings_tokenizer_repo"
							placeholder="owner/name"
							value={repo}
						></input>
					</p>
					<p>
						<label for="embeddings_batch_size">Batch Size</label>
						<input
							type="number"
							min="1"
							id="embeddings_batch_size"
							name="embeddings_batch_size"
							value={String(
								remoteEmbeddings?.settings.batch_size ??
									DEFAULT_REMOTE_BATCH_SIZE,
							)}
						></input>
					</p>
					<p class="error">
						Every document you ingest will be sent to the provider. If the
						provider changes or retires the model, documents must be
						re-ingested, so pick a pinned model version where one is offered.
					</p>
				</div>
				<p>
					<small>
						The language model which will be used to vectorize documents. This
						cannot be changed after installing as it would invalidate all
						existing data.
					</small>
				</p>
			</fieldset>
			<fieldset>
				<legend>Model Providers</legend>
				<ProviderList
					formPrefix="install_"
					credentialModes
					providers={config.providers.map((provider) => ({
						provider,
						hasKey: !!keys[provider.id],
					}))}
				></ProviderList>
			</fieldset>
			<LoggingFieldset></LoggingFieldset>
			<HostsAndPortsFieldset></HostsAndPortsFieldset>
			<fieldset>
				<legend>Security Level</legend>
				<p>
					<input
						type="radio"
						value="none"
						id="security_none"
						name="security_level"
						checked={!securityEnabled}
					></input>
					<label for="security_none">None</label>
				</p>
				<p>
					<input
						type="radio"
						value="demo"
						id="security_demo"
						name="security_level"
						checked={demoEnabled}
					></input>
					<label for="security_demo">Demo</label>
				</p>
				<p>
					<input
						type="radio"
						value="enabled"
						id="security_enabled"
						name="security_level"
						checked={securityEnabled && !demoEnabled}
					></input>
					<label for="security_enabled">Enabled</label>
				</p>
				<p>
					If you don't enable security anyone who can access the web UI will
					have full privileges to interact with your Shabti instance, and use
					any provider API keys you configure!
				</p>
				<p>
					The demo configuration should never be used for production as it is a
					very insecure configuration designed to show off the different access
					levels using test users.
				</p>
				<p id="keycloak_config">
					<label for="keycloak_password_first">Keycloak Admin Password</label>
					<input type="password" id="keycloak_password_first"></input>
					<label for="keycloak_password">Confirm Keycloak Admin Password</label>
					<input
						type="password"
						id="keycloak_password"
						name="keycloak_password"
					></input>
				</p>
				<div id="password_status" class="error"></div>
			</fieldset>
			{props.currentVersion && (
				<p id="install_warning" class="error">
					Installing will remove your existing installation. All document
					collections and the documents they contain will be permanently lost.
				</p>
			)}
			<button type="submit" id="install_submit" class="install_button">
				Start Installation!
			</button>
		</form>
	);
};
