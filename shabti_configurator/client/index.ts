import { zxcvbn, zxcvbnOptions } from "@zxcvbn-ts/core";
import * as zxcvbnCommonPackage from "@zxcvbn-ts/language-common";
import * as zxcvbnEnPackage from "@zxcvbn-ts/language-en";
import {
	type VNode,
	type VNodeChildren,
	attributesModule,
	classModule,
	eventListenersModule,
	h,
	init,
	propsModule,
	styleModule,
	toVNode,
} from "snabbdom";
import TomSelect from "tom-select";
import "tom-select/dist/css/tom-select.css";
import { providerId, qualify } from "../providerIds";

const patch = init([
	// Init patch function with chosen modules
	classModule, // makes it easy to toggle classes
	propsModule, // for setting properties on DOM elements
	styleModule, // handles styling on elements with support for animations
	eventListenersModule, // attaches event listeners
	attributesModule, // for setting attributes on DOM elements
]);

const options = {
	translations: zxcvbnEnPackage.translations,
	graphs: zxcvbnCommonPackage.adjacencyGraphs,
	dictionary: {
		...zxcvbnCommonPackage.dictionary,
		...zxcvbnEnPackage.dictionary,
	},
};

zxcvbnOptions.setOptions(options);

const password1El = document.getElementById(
	"keycloak_password_first",
) as HTMLInputElement;
const password2El = document.getElementById(
	"keycloak_password",
) as HTMLInputElement;
const formSubmitEls = document.querySelectorAll(".install_button");
const formEl = document.getElementById(
	"install_form",
) as HTMLFormElement | null;
let passwordStatus: VNode = toVNode(
	document.getElementById("password_status")!,
);
let formErrors: VNode = toVNode(document.getElementById("form_errors")!);
let formSuccess: VNode = toVNode(document.getElementById("form_success")!);

const patchPassword = (contents: VNodeChildren) => {
	passwordStatus = patch(
		passwordStatus,
		h("div#password_status.error", contents),
	);
};

const patchFormErrors = (contents: VNodeChildren) => {
	formErrors = patch(formErrors, h("div#form_errors.error", contents));
};

const patchFormSuccess = (contents: VNodeChildren) => {
	formSuccess = patch(formSuccess, h("div#form_success.success", contents));
};

// painted before any of the wiring below, which is allowed to find the elements it works on
// missing: a message the user has to see can't depend on the rest of the page being there
const params = new URLSearchParams(window.location.search);
const err = params.get("err");
if (err == "invalid-form") patchFormErrors("Form data was invalid");
// the same banner reports uninstall, model, settings and launch failures, and the messages name
// what went wrong themselves
else if (err) patchFormErrors([h("p", "An error occurred:"), h("p", err)]);
const success = params.get("done");
if (success) patchFormSuccess(success);
window.history.replaceState(null, "", "/"); // delete the URL params after patching the page

// the native multiple select needs ctrl-clicking and gives no indication that more than one
// option can be picked, so we replace it with a tag style widget. Tom Select keeps the original
// select in the DOM and mirrors the selection onto it, so the form submission and the
// onchange handler below carry on working as they did.
const wireMultiSelect = (selectId: string) => {
	const el = document.getElementById(selectId) as HTMLSelectElement | null;
	if (!el) return; // the section this belongs to isn't on the page
	new TomSelect(el, { plugins: ["remove_button"] });
};

// the models the provider fieldsets of a form offer, qualified with the ids their names will give
// them. the template more fieldsets are copied from isn't searched, its contents aren't in the DOM
const fieldsetModels = (form: HTMLFormElement) =>
	[
		...form.querySelectorAll<HTMLInputElement>('input[name="provider_prefix"]'),
	].flatMap(({ value: prefix }) => {
		const name = document.getElementById(
			`${prefix}provider_name`,
		) as HTMLInputElement | null;
		const select = document.getElementById(
			`${prefix}provider_model`,
		) as HTMLSelectElement | null;
		const id = providerId(name?.value ?? "");
		if (!select || !id) return [];
		return [...select.selectedOptions].map((option) =>
			qualify(id, option.value),
		);
	});

// the default chat model can only be chosen when more than one model is on offer, so the
// selector is rendered on the fly. Both the install form and the model management form have
// one, hence the ids being parameters, they have to be unique across the page. The models of
// providers that are already configured are on the container, and on the install form the ones
// picked for the providers being added are offered too.
const wireDefaultModelSelector = (
	selectId: string,
	containerId: string,
	withAddedProviders = false,
) => {
	const chatModelSelector = document.getElementById(
		selectId,
	) as HTMLSelectElement | null;
	const container = document.getElementById(containerId);
	if (!chatModelSelector || !container) return; // the section this belongs to isn't on the page
	const selectorId = `${containerId}_select`;
	const noChatModelNote = document.getElementById(
		`${containerId}_no_chat_model`,
	);
	const configured = JSON.parse(
		container.dataset.remoteModels || "[]",
	) as string[];
	let vnode: VNode = toVNode(container);
	const update = () => {
		const selected = [...chatModelSelector.selectedOptions].map((s) => s.value);
		const choices = [
			...selected,
			...configured,
			...(withAddedProviders && chatModelSelector.form
				? fieldsetModels(chatModelSelector.form)
				: []),
		];
		// a provider's models answer prompts as well as local ones do
		noChatModelNote?.classList.toggle("hidden", choices.length > 0);
		// keep the current choice if it's still on offer, otherwise the browser would silently
		// fall back to the first option
		const previous = (
			document.getElementById(selectorId) as HTMLSelectElement | null
		)?.value;
		const options = choices.map((model) =>
			h(
				"option",
				{ attrs: { value: model, selected: model == previous } },
				model,
			),
		);
		vnode = patch(
			vnode,
			h(
				`div#${containerId}`,
				{ attrs: { "data-remote-models": JSON.stringify(configured) } },
				choices.length > 1
					? h("p", [
							h("label", { attrs: { for: selectorId } }, "Default Chat Model"),
							h(
								"select",
								{ attrs: { id: selectorId, name: "default_model" } },
								options,
							),
							"This model will be selected by default unless the user chooses a different one.",
						])
					: undefined,
			),
		);
	};
	// the whole form rather than the chat model selector, as the provider's models and name are
	// in it too. The default selector's own changes come through here as well, which is harmless
	chatModelSelector.form?.addEventListener("change", (e) => {
		if ((e.target as HTMLElement | null)?.id != selectorId) update();
	});
};

// what a provider fieldset announces each time its models change, for the install form's
// embeddings model
type ProviderModels = {
	prefix: string;
	name: string;
	models: string[];
	embeddingsModels: string[];
};

const announceModels = (el: Element, detail: ProviderModels) =>
	el.dispatchEvent(
		new CustomEvent("provider-models", { detail, bubbles: true }),
	);

// asks the configurator to list the provider's models, which it does from the server so the
// browser doesn't need the provider to allow it, then offers them to choose from
const wireProviderTest = (prefix: string) => {
	const button = document.getElementById(
		`${prefix}provider_test`,
	) as HTMLButtonElement | null;
	const statusEl = document.getElementById(`${prefix}provider_test_status`);
	const modelsEl = document.getElementById(`${prefix}provider_models`);
	if (!button || !statusEl || !modelsEl) return; // the section this belongs to isn't on the page
	const field = (name: string) =>
		document.getElementById(`${prefix}${name}`) as
			| HTMLInputElement
			| HTMLTextAreaElement;
	// set on a fieldset prefilled from an installed provider
	const existing = document.getElementById(
		`${prefix}provider_existing`,
	) as HTMLInputElement | null;
	let status: VNode = toVNode(statusEl);
	let models: VNode = toVNode(modelsEl);
	// a fieldset prefilled from an installed provider starts with its models
	const prefilled = document.getElementById(
		`${prefix}provider_model`,
	) as HTMLSelectElement | null;
	let tomSelect: TomSelect | undefined = prefilled
		? new TomSelect(prefilled, { plugins: ["remove_button"] })
		: undefined;
	const showStatus = (className: string, contents: VNodeChildren) => {
		status = patch(
			status,
			h(`div#${prefix}provider_test_status${className}`, contents),
		);
	};
	const showModels = (ids: string[]) => {
		tomSelect?.destroy();
		tomSelect = undefined;
		const selectId = `${prefix}provider_model`;
		models = patch(
			models,
			h(
				`div#${prefix}provider_models`,
				ids.length
					? h("p", [
							h("label", { attrs: { for: selectId } }, "Models to Offer"),
							h(
								"select",
								{
									attrs: {
										id: selectId,
										name: selectId,
										multiple: true,
									},
								},
								ids.map((id) => h("option", { attrs: { value: id } }, id)),
							),
						])
					: undefined,
			),
		);
		const select = document.getElementById(
			selectId,
		) as HTMLSelectElement | null;
		if (select)
			tomSelect = new TomSelect(select, { plugins: ["remove_button"] });
		// so the default chat model selector drops the models of a provider that changed
		modelsEl.dispatchEvent(new Event("change", { bubbles: true }));
	};
	// for the install form's embeddings model, which can be any of them
	const announce = (models: string[], embeddingsModels: string[]) =>
		announceModels(modelsEl, {
			prefix,
			name: field("provider_name").value.trim(),
			models,
			embeddingsModels,
		});
	button.onclick = async () => {
		button.disabled = true;
		showStatus("", "Testing the connection...");
		try {
			const response = await fetch("/test-provider", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					base_url: field("provider_base_url").value,
					key: field("provider_key").value,
					headers: field("provider_headers").value,
					// for the configurator to test an installed provider with the key it has
					existing: existing?.value,
				}),
			});
			const result = await response.json();
			if (result.error) {
				showModels([]);
				announce([], []);
				showStatus(".error", [
					h("p", result.error),
					result.hint ? h("p", result.hint) : undefined,
				]);
				return;
			}
			// the URL the API will use, which may have lost an endpoint path pasted along with it
			field("provider_base_url").value = result.baseUrl;
			showModels(result.models);
			announce(result.models, result.embeddingsModels);
			showStatus(
				"",
				result.models.length
					? `Found ${result.models.length} models, choose the ones to offer.`
					: result.embeddingsModels.length
						? `Found ${result.embeddingsModels.length} embeddings models.`
						: "The provider listed no models.",
			);
		} catch {
			showStatus(".error", "Couldn't test the connection.");
		} finally {
			button.disabled = false;
		}
	};
};

// the install form's providers can embed as well. each one's embeddings models come first, but any
// of its models can be chosen, as not every provider says which of them embed. searchable, as a
// provider can list hundreds. a model is named after the fieldset it came from as `prefix:model`,
// for the server to tell which provider it belongs to
const wireRemoteEmbeddings = () => {
	const select = document.getElementById(
		"remote_embeddings_model",
	) as HTMLSelectElement | null;
	if (!select || !formEl) return; // the install form isn't on the page
	const tomSelect = new TomSelect(select, {
		maxItems: 1,
		lockOptgroupOrder: true,
		placeholder: "Search the provider's models",
	});
	formEl.addEventListener("provider-models", (e) => {
		const { prefix, name, models, embeddingsModels } = (
			e as CustomEvent<ProviderModels>
		).detail;
		const previous = tomSelect.getValue() as string;
		// the provider's models from before, which the ones it lists now replace
		for (const value of Object.keys(tomSelect.options))
			if (value.startsWith(`${prefix}:`)) tomSelect.removeOption(value, true);
		const embeddingsGroup = `${prefix}embeddings`;
		const otherGroup = `${prefix}other`;
		tomSelect.removeOptionGroup(embeddingsGroup);
		tomSelect.removeOptionGroup(otherGroup);
		const label = (text: string) => (name ? `${name}: ${text}` : text);
		tomSelect.addOptionGroup(embeddingsGroup, {
			label: label("Embeddings models"),
		});
		tomSelect.addOptionGroup(otherGroup, { label: label("Other models") });
		for (const id of embeddingsModels)
			tomSelect.addOption({
				value: `${prefix}:${id}`,
				text: id,
				optgroup: embeddingsGroup,
			});
		for (const id of models)
			tomSelect.addOption({
				value: `${prefix}:${id}`,
				text: id,
				optgroup: otherGroup,
			});
		const chosen = tomSelect.options[previous]
			? previous
			: embeddingsModels.length == 1
				? `${prefix}:${embeddingsModels[0]}`
				: undefined;
		if (chosen) tomSelect.setValue(chosen, true);
		else tomSelect.clear(true);
	});
};

// a provider fieldset is titled with the name typed into it
const wireProviderLegend = (prefix: string) => {
	const name = document.getElementById(
		`${prefix}provider_name`,
	) as HTMLInputElement | null;
	const legend = document.getElementById(`${prefix}provider_legend`);
	if (!name || !legend) return;
	const update = () =>
		(legend.textContent = name.value.trim() || "New Model Provider");
	update(); // the browser may have restored a name when the page was reloaded
	name.addEventListener("input", update);
};

const wireProviderFieldset = (fieldset: HTMLElement, prefix: string) => {
	wireProviderTest(prefix);
	wireProviderLegend(prefix);
	const remove = fieldset.querySelector<HTMLButtonElement>(".provider_remove");
	if (remove)
		remove.onclick = () => {
			const form = fieldset.closest("form");
			// so the embeddings model selector drops the provider's models
			announceModels(fieldset, {
				prefix,
				name: "",
				models: [],
				embeddingsModels: [],
			});
			fieldset.remove();
			// so the default chat model selector drops them too
			form?.dispatchEvent(new Event("change"));
		};
};

// a form starts with one provider fieldset, or one for each installed provider on the install
// form, and the template rendered alongside them adds more. each is numbered after the ones before
// it, so its fields get a prefix of their own
const wireProviderList = (formPrefix: string) => {
	const container = document.getElementById(`${formPrefix}providers`);
	const template = document.getElementById(
		`${formPrefix}provider_template`,
	) as HTMLTemplateElement | null;
	const addButton = document.getElementById(`${formPrefix}provider_add`);
	if (!container || !template || !addButton) return; // the form isn't on the page
	for (const fieldset of container.children)
		wireProviderFieldset(
			fieldset as HTMLElement,
			fieldset.querySelector<HTMLInputElement>('input[name="provider_prefix"]')!
				.value,
		);
	let count = container.children.length;
	addButton.onclick = () => {
		const prefix = `${formPrefix}${count}_`;
		container.insertAdjacentHTML(
			"beforeend",
			template.innerHTML.replaceAll("{n}", String(count)),
		);
		count++;
		const fieldset = container.lastElementChild as HTMLElement;
		wireProviderFieldset(fieldset, prefix);
		// so the install form hides the key mode when users' own keys aren't available
		fieldset.dispatchEvent(new Event("change", { bubbles: true }));
	};
};

// a provider taking only users' own keys still needs the shared one, which a note under its key
// mode says
const setSharedKeyNote = (field: Element) =>
	field
		.querySelector(".shared_key_note")
		?.classList.toggle(
			"hidden",
			field.querySelector("select")?.value != "user",
		);

// every form's key modes at once, including those of fieldsets added later
const wireSharedKeyNotes = () => {
	document.querySelectorAll(".credential_mode_field").forEach(setSharedKeyNote);
	document.addEventListener("change", (e) => {
		const field = (e.target as Element | null)?.closest?.(
			".credential_mode_field",
		);
		if (field) setSharedKeyNote(field);
	});
};

// uninstalling and installing over an existing configuration both destroy the user's data and
// can't be undone, so we make them confirm what they're about to lose. The warning is built
// here rather than server side because it depends on the state of the form
const wireDataLossConfirmation = (
	formId: string,
	question: string,
	extraWarnings: () => string[],
) => {
	const form = document.getElementById(formId) as HTMLFormElement | null;
	if (!form) return; // the section this belongs to isn't on the page
	form.onsubmit = (e) => {
		const warnings = [
			question,
			"All document collections and the documents they contain will be permanently lost.",
			...extraWarnings(),
		];
		if (!window.confirm(warnings.join("\n\n"))) e.preventDefault();
	};
};

// the log directory only applies when logging is on, and both the install form and the
// settings management form have their own copy of the fieldset
const wireLoggingVisibility = (toggleId: string) => {
	const toggle = document.getElementById(toggleId) as HTMLInputElement | null;
	const fieldset = toggle?.closest("fieldset");
	if (!toggle || !fieldset) return; // the section this belongs to isn't on the page
	const loggingEls = fieldset.querySelectorAll(".logging_element");
	const setLoggingVisibility = () =>
		loggingEls.forEach((el) => el.classList.toggle("hidden", !toggle.checked));
	setLoggingVisibility();
	toggle.onchange = setLoggingVisibility;
};

const wireTabs = () => {
	const nav = document.getElementById("tab_nav");
	if (!nav) return; // only one section is available, so there's nothing to switch between
	const tabs = [...nav.querySelectorAll<HTMLButtonElement>(".tab")];
	tabs.forEach((tab) => {
		tab.onclick = () =>
			tabs.forEach((other) => {
				other.classList.toggle("active", other == tab);
				document
					.getElementById(other.dataset.tab!)
					?.classList.toggle("hidden", other != tab);
			});
	});
};

const enableSubmit = () =>
	formSubmitEls.forEach((el) => ((el as HTMLButtonElement).disabled = false));

const disableSubmit = () =>
	formSubmitEls.forEach((el) => ((el as HTMLButtonElement).disabled = true));

const checkPasswords = () => {
	if (!password1El || !password2El) {
		patchPassword(null);
		enableSubmit();
		return;
	}
	const password1 = password1El.value;
	const password2 = password2El.value;
	if (!password1 && !password2) {
		patchPassword(h("p", "please provide a strong password!"));
		disableSubmit();
		return;
	}
	if (password1 && !password2) {
		patchPassword(h("p", "please confirm the password!"));
		disableSubmit();
		return;
	}
	if (password1 != password2) {
		patchPassword(h("p", "passwords don't match!"));
		disableSubmit();
		return;
	}
	const strength = zxcvbn(password2);
	if (strength.score < 4) {
		patchPassword([
			h("p", "Password is too weak!"),
			strength.feedback.warning
				? h("p", `warning: ${strength.feedback.warning}`)
				: undefined,
			strength.feedback.suggestions.length ? h("p", "suggestions:") : undefined,
			...strength.feedback.suggestions.map((suggestion) => h("p", suggestion)),
		]);
		disableSubmit();
		return;
	}
	patchPassword(null);
	enableSubmit();
};

if (password2El) password2El.oninput = checkPasswords;

const setFormVisibility = () => {
	if (!formEl) return; // the install form isn't on the page
	const formData = new FormData(formEl);
	const remoteEmbeddings = formData.get("embeddings_source") == "provider";
	document
		.getElementById("local_embeddings")
		?.classList.toggle("hidden", remoteEmbeddings);
	document
		.getElementById("remote_embeddings")
		?.classList.toggle("hidden", !remoteEmbeddings);
	document
		.getElementById("embeddings_tokenizer_repo_container")
		?.classList.toggle("hidden", formData.get("embeddings_tokenizer") != "hf");
	// users' own keys need security to be fully enabled, so it's back to the shared key otherwise
	const userKeys = formData.get("security_level") == "enabled";
	formEl
		.querySelectorAll<HTMLElement>(".credential_mode_field")
		.forEach((field) => {
			field.classList.toggle("hidden", !userKeys);
			const credentialMode = field.querySelector("select");
			if (credentialMode && !userKeys) credentialMode.value = "shared";
			// setting the value fires no change event
			setSharedKeyNote(field);
		});
	const keycloakConfig = document.getElementById("keycloak_config");
	if (formData.get("security_level") != "none") {
		keycloakConfig?.classList.remove("hidden");
		checkPasswords();
	} else {
		keycloakConfig?.classList.add("hidden");
		patchPassword(null);
		enableSubmit();
	}
};
if (formEl) {
	setFormVisibility();
	formEl.onchange = setFormVisibility;
}

wireMultiSelect("language_model");
wireMultiSelect("manage_language_model");
wireDefaultModelSelector("language_model", "default_model_selector", true);
wireDefaultModelSelector(
	"manage_language_model",
	"manage_default_model_selector",
);
wireProviderList("install_");
wireProviderList("manage_");
wireRemoteEmbeddings();
wireSharedKeyNotes();
wireLoggingVisibility("activity_logging");
wireLoggingVisibility("manage_activity_logging");
wireTabs();
wireDataLossConfirmation(
	"uninstall_form",
	"Are you sure you want to uninstall Shabti?",
	() =>
		(document.getElementById("delete_model_files") as HTMLInputElement | null)
			?.checked
			? [
					"The language model files will be deleted, and will have to be downloaded again if you install Shabti in the future.",
				]
			: [],
);
// the warning is only rendered when there's an existing installation for the install to
// destroy, so a first time install doesn't get a dialog
if (document.getElementById("install_warning"))
	wireDataLossConfirmation(
		"install_form",
		"Are you sure you want to install Shabti over your existing installation?",
		() => [],
	);
