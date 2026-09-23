import getEnvs from "./getEnvs";
import { HostsAndPortsFieldset } from "./hostsAndPortsFieldset";
import { LoggingFieldset } from "./loggingFieldset";
import { VersionSelector } from "./versionSelector";
import currentIsLocal from "./currentIsLocal";
import listCompatibleVersions from "./listCompatibleVersions";
import { ConnectivityNotice } from "./connectivityNotice";
import {
	ChatModelSelector,
	ModelSelectionFallback,
	resolveModelSelection,
} from "./chatModelSelector";

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
	} = await resolveModelSelection({
		fallback: ModelSelectionFallback.DefaultModelOnly,
	});
	const versions = await listCompatibleVersions();
	if (
		(!versions.length && !props.devMode) ||
		!chatModels.length ||
		!embeddingsModels.length
	)
		return <p class="error">You need to be online to install Shabti.</p>;
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
					defaultModel={selection.defaultModel}
				></ChatModelSelector>
				<p>
					<label for="embeddings_model">Select Embeddings Model</label>
					<select name="embeddings_model" id="embeddings_model">
						{embeddingsModels.map((k) => (
							<option value={k} selected={k == selection.embeddingsModel}>
								{k}
							</option>
						))}
					</select>
				</p>
				<p>
					<small>
						The language model which will be used to vectorize documents. This
						cannot be changed after installing as it would invalidate all
						existing data.
					</small>
				</p>
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
					have full privileges to interact with your Shabti instance!
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
