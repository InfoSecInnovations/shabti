import { ProviderFieldset } from "./providerFieldset";
import { readProviders } from "./writeProviders";

export const ProviderManagementForm = async () => {
	const { config, keys } = await readProviders();
	return (
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
				<ProviderFieldset
					idPrefix="manage_"
					legend="Add a Model Provider"
				></ProviderFieldset>
				<button type="submit">Apply Provider Changes</button>
			</fieldset>
		</form>
	);
};
