import type { CredentialMode, ProviderConfig } from "./writeProviders";

const CREDENTIAL_MODE_LABELS: Record<CredentialMode, string> = {
	shared: "Shared key",
	user: "Each user's own key",
	shared_or_user: "User's own key, or the shared one",
};

// users' own keys need security to be fully enabled, so this is only offered where it is
export const CredentialModeSelect = (props: {
	id: string;
	name: string;
	selected?: CredentialMode;
}) => (
	<p id={`${props.id}_field`} class="credential_mode_field">
		<label for={props.id}>API Keys</label>
		<select id={props.id} name={props.name}>
			{Object.entries(CREDENTIAL_MODE_LABELS).map(([mode, label]) => (
				<option value={mode} selected={mode == (props.selected || "shared")}>
					{label}
				</option>
			))}
		</select>
	</p>
);

// a form can add several providers at once, so the ids and names of each one's fields are
// prefixed to tell them apart, and the prefix is submitted for the server to find them by.
// wireProviderTest in the client bundle relies on the ids, and fills `${prefix}provider_models`
// with the models the test finds. The legend follows the name as it's typed in.
// A fieldset prefilled from an installed provider names it, for the server to keep its key when
// none is typed, as the key itself never goes into the page
export const ProviderFieldset = (props: {
	prefix: string;
	credentialModes?: boolean;
	removable?: boolean;
	provider?: ProviderConfig;
	hasKey?: boolean;
}) => {
	const { prefix, provider } = props;
	const models = provider?.models?.allow ?? [];
	return (
		<fieldset>
			<legend id={`${prefix}provider_legend`}>
				{provider?.name || "New Model Provider"}
			</legend>
			<input type="hidden" name="provider_prefix" value={prefix}></input>
			{provider ? (
				<input
					type="hidden"
					id={`${prefix}provider_existing`}
					name={`${prefix}provider_existing`}
					value={provider.id}
				></input>
			) : undefined}
			<p>
				<small>
					Any OpenAI-compatible API, hosted or self-hosted, can answer prompts
					alongside or instead of the local models.
				</small>
			</p>
			<p>
				<label for={`${prefix}provider_name`}>Name</label>
				<input
					type="text"
					id={`${prefix}provider_name`}
					name={`${prefix}provider_name`}
					value={provider?.name}
				></input>
			</p>
			<p>
				<label for={`${prefix}provider_base_url`}>Base URL</label>
				<input
					type="text"
					id={`${prefix}provider_base_url`}
					name={`${prefix}provider_base_url`}
					placeholder="https://api.example.com/v1"
					value={provider?.base_url}
				></input>
			</p>
			<p>
				<label for={`${prefix}provider_key`}>
					{props.hasKey ? "Replace API Key" : "API Key"}
				</label>
				<input
					type="password"
					id={`${prefix}provider_key`}
					name={`${prefix}provider_key`}
					autocomplete="off"
					placeholder={props.hasKey ? "set" : undefined}
				></input>
			</p>
			{props.hasKey ? undefined : (
				<p>
					<small>Leave it empty for a provider that doesn't take one.</small>
				</p>
			)}
			{props.credentialModes ? (
				<CredentialModeSelect
					id={`${prefix}provider_credential_mode`}
					name={`${prefix}provider_credential_mode`}
					selected={provider?.credential_mode}
				></CredentialModeSelect>
			) : undefined}
			<p>
				<label for={`${prefix}provider_headers`}>Extra Headers</label>
				<textarea
					id={`${prefix}provider_headers`}
					name={`${prefix}provider_headers`}
					rows={2}
					placeholder="Name: value"
				>
					{Object.entries(provider?.headers ?? {})
						.map(([name, value]) => `${name}: ${value}`)
						.join("\n")}
				</textarea>
			</p>
			<p>
				<button type="button" id={`${prefix}provider_test`}>
					Test Connection
				</button>
				{props.removable ? (
					<button type="button" class="provider_remove">
						Remove
					</button>
				) : undefined}
			</p>
			<div id={`${prefix}provider_test_status`}></div>
			{/* the same select the client shows after a test, so the form can go without one */}
			<div id={`${prefix}provider_models`}>
				{models.length ? (
					<p>
						<label for={`${prefix}provider_model`}>Models to Offer</label>
						<select
							id={`${prefix}provider_model`}
							name={`${prefix}provider_model`}
							multiple
						>
							{models.map((model) => (
								<option value={model} selected>
									{model}
								</option>
							))}
						</select>
					</p>
				) : undefined}
			</div>
		</fieldset>
	);
};

// the fieldsets a form adds providers with. wireProviderList in the client bundle copies the
// template for each one added, numbering it in place of {n}. Both the install form and the
// provider management form have one, so formPrefix keeps the ids unique on the page. The install
// form starts with the installed providers, so a reinstall keeps them unless they're removed
export const ProviderList = (props: {
	formPrefix: string;
	credentialModes?: boolean;
	providers?: { provider: ProviderConfig; hasKey: boolean }[];
}) => (
	<>
		<div id={`${props.formPrefix}providers`}>
			{props.providers?.length ? (
				props.providers.map(({ provider, hasKey }, i) => (
					<ProviderFieldset
						prefix={`${props.formPrefix}${i}_`}
						credentialModes={props.credentialModes}
						removable
						provider={provider}
						hasKey={hasKey}
					></ProviderFieldset>
				))
			) : (
				<ProviderFieldset
					prefix={`${props.formPrefix}0_`}
					credentialModes={props.credentialModes}
				></ProviderFieldset>
			)}
		</div>
		<template id={`${props.formPrefix}provider_template`}>
			<ProviderFieldset
				prefix={`${props.formPrefix}{n}_`}
				credentialModes={props.credentialModes}
				removable
			></ProviderFieldset>
		</template>
		<p>
			<button type="button" id={`${props.formPrefix}provider_add`}>
				Add Another Provider
			</button>
		</p>
	</>
);
