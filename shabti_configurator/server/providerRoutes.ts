import { Hono } from "hono";
import { storedProvider, testProvider } from "./providers";
import { readProviders } from "./writeProviders";

export const providerRoutes = new Hono().post("/test-provider", async (c) => {
	// JSON alone, which a page on another site can only send after a preflight the configurator
	// never answers, so it can't have the configurator make requests for it
	if (!c.req.header("Content-Type")?.startsWith("application/json"))
		return c.json({ error: "Expected a JSON body." }, 415);
	let body: {
		base_url?: unknown;
		key?: unknown;
		headers?: unknown;
		existing?: unknown;
	};
	try {
		body = await c.req.json();
	} catch {
		return c.json({ error: "Expected a JSON body." }, 400);
	}
	const text = (value: unknown) => (typeof value == "string" ? value : "");
	const baseUrl = text(body.base_url);
	// an installed provider the install form was prefilled with keeps its key out of the page
	const existing = text(body.existing);
	const key =
		text(body.key) ||
		(existing
			? storedProvider(await readProviders(), existing, baseUrl)?.key
			: undefined);
	return c.json(
		await testProvider({ baseUrl, key, headers: text(body.headers) }),
	);
});
