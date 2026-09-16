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

// the default chat model can only be chosen when more than one model is selected, so the
// selector is rendered on the fly. Both the install form and the model management form have
// one, hence the ids being parameters, they have to be unique across the page.
const wireDefaultModelSelector = (selectId: string, containerId: string) => {
	const chatModelSelector = document.getElementById(
		selectId,
	) as HTMLSelectElement | null;
	const container = document.getElementById(containerId);
	if (!chatModelSelector || !container) return; // the section this belongs to isn't on the page
	const selectorId = `${containerId}_select`;
	let vnode: VNode = toVNode(container);
	chatModelSelector.onchange = () => {
		const selected = [...chatModelSelector.selectedOptions].map((s) => s.value);
		// keep the current choice if it's still selected, otherwise the browser would silently
		// fall back to the first option
		const previous = (
			document.getElementById(selectorId) as HTMLSelectElement | null
		)?.value;
		const options = selected.map((model) =>
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
				selected.length > 1
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
wireDefaultModelSelector("language_model", "default_model_selector");
wireDefaultModelSelector(
	"manage_language_model",
	"manage_default_model_selector",
);
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
