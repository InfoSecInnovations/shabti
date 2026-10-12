// shared with the client bundle, which shows the ids a provider's models will have before the form
// is submitted, so nothing here can use anything Node has and a browser doesn't

/** llama.cpp's id, which no configured provider may take */
export const LOCAL_PROVIDER = "local";

/** the id a provider is stored under, from the name it is given: what the API accepts as one */
export const providerId = (name: string) =>
	name
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");

export const qualify = (provider: string, model: string) =>
	`${provider}/${model}`;
