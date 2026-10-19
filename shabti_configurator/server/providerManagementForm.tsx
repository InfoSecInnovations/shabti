import getEnvs from "./getEnvs";
import { CredentialModeSelect, ProviderList } from "./providerFieldset";
import { userKeysEnabled } from "./userKeys";
import { readProviders } from "./writeProviders";

export const ProviderManagementForm = async () => {
	const { config, keys } = await readProviders();
	const userKeys = userKeysEnabled(await getEnvs());
	const embeddingsProvider = config.embeddings?.model.split("/")[0];
	return (
		<>
			<form action="/manage-providers" method="post">
				<fieldset>
					<legend>Model Providers</legend>
					{config.providers.length ? (
						config.providers.map((provider) => (
							<fieldset>
								<legend>{provider.name}</legend>
								<p>{provider.base_url}</p>
								<p>
									<small>
										Models: {provider.models?.allow?.join(", ") || "all"}
									</small>
								</p>
								<p>
									<label for={`key_${provider.id}`}>Replace API Key</label>
									<input
										type="password"
										id={`key_${provider.id}`}
										name={`key_${provider.id}`}
										autocomplete="off"
										placeholder={keys[provider.id] ? "set" : "not set"}
									></input>
								</p>
								{/* the provider embedding the documents can only use the shared key */}
								{userKeys && provider.id != embeddingsProvider ? (
									<CredentialModeSelect
										id={`mode_${provider.id}`}
										name={`mode_${provider.id}`}
										selected={provider.credential_mode}
									></CredentialModeSelect>
								) : undefined}
								<p>
									<input
										type="checkbox"
										id={`remove_provider_${provider.id}`}
										name="remove_provider"
										value={provider.id}
									></input>
									<label for={`remove_provider_${provider.id}`}>Remove</label>
								</p>
							</fieldset>
						))
					) : (
						<p>No model providers are configured.</p>
					)}
					<ProviderList
						formPrefix="manage_"
						credentialModes={userKeys}
					></ProviderList>
					<button type="submit">Apply Provider Changes</button>
				</fieldset>
			</form>
			{userKeys ? (
				<form action="/rotate-secrets-key" method="post">
					<fieldset>
						<legend>Users' API Keys</legend>
						<p>
							<small>
								Re-encrypts the API keys users have added under a new encryption
								key.
							</small>
						</p>
						<button type="submit">Rotate Encryption Key</button>
					</fieldset>
				</form>
			) : undefined}
		</>
	);
};
