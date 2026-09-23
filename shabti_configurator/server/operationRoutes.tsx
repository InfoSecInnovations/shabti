import { type Context, Hono } from "hono";
import type { Child } from "hono/jsx";
import { streamSSE } from "hono/streaming";
import {
	BACK,
	INTERRUPTED,
	type OperationEvent,
	type OperationUpdate,
} from "./operationProtocol";
import { getOperation, startOperation } from "./operations";

const page = (content: Child) => (
	<html>
		<head>
			<link rel="stylesheet" href="/style.css" />
			<script type="module" src="/progress.js"></script>
		</head>
		<body>{content}</body>
	</html>
);

/**
 * Starts the operation and sends the browser to the page which watches it. EventSource can only GET,
 * so the form's POST starts the work and the redirect is what hands it over to the page.
 */
export const runOperation = (
	c: Context,
	title: string,
	updates: AsyncIterable<OperationUpdate>,
	endMessage?: string,
) =>
	c.redirect(`/operations/${startOperation(title, updates, endMessage)}`, 303);

export const operationRoutes = new Hono()
	.get("/:id", (c) => {
		const id = c.req.param("id");
		const operation = getOperation(id);
		// the configurator restarted since, or a newer operation replaced this one
		if (!operation)
			return c.html(
				page(
					<>
						<p class="error">{INTERRUPTED}</p>
						<p>
							<a href="/">{BACK}</a>
						</p>
					</>,
				),
				404,
			);
		return c.html(
			page(
				<>
					<h1>{operation.title}</h1>
					<shabti-operation
						events={`/operations/${id}/events`}
					></shabti-operation>
				</>,
			),
		);
	})
	.get("/:id/events", (c) => {
		const operation = getOperation(c.req.param("id"));
		if (!operation) return c.body(null, 404);
		return streamSSE(c, async (stream) => {
			// writes go out one at a time in the order they were queued, so a live update can't
			// overtake the replay, and the terminal status is always the last thing sent
			let queue = Promise.resolve();
			const send = ({ event, data }: OperationEvent) => {
				const message = { event, data: JSON.stringify(data) };
				queue = queue.then(() => stream.writeSSE(message));
			};
			operation.snapshot().forEach(send);
			if (!operation.finished)
				await new Promise<void>((resolve) => {
					const unsubscribe = operation.subscribe((event) => {
						send(event);
						if (event.event == "status") {
							unsubscribe();
							resolve();
						}
					});
					stream.onAbort(() => {
						unsubscribe();
						resolve();
					});
				});
			await queue;
		});
	});
