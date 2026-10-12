import { Hono } from "hono";
import { testProvider } from "./providers";

export const providerRoutes = new Hono().post("/test-provider", async (c) => {
	// JSON alone, which a page on another site can only send after a preflight the configurator
	// never answers, so it can't have the configurator make requests for it
	if (!c.req.header("Content-Type")?.startsWith("application/json"))
		return c.json({ error: "Expected a JSON body." }, 415);
	let body: { base_url?: unknown; key?: unknown; headers?: unknown };
	try {
		body = await c.req.json();
	} catch {
		return c.json({ error: "Expected a JSON body." }, 400);
	}
	const text = (value: unknown) => (typeof value == "string" ? value : "");
	return c.json(
		await testProvider({
			baseUrl: text(body.base_url),
			key: text(body.key),
			headers: text(body.headers),
		}),
	);
});
