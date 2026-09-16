import type { Context } from "hono";
import { stream } from "hono/streaming";
import type { StreamingApi } from "hono/utils/stream";
import describeError from "./describeError";

const INTERRUPTED =
	"The operation stopped before it finished, check the configurator's terminal output.";

// what the user sees when the operation didn't get to the end. We stay on the streaming page rather
// than redirecting so the whole progress log is still there to read alongside the error
const failureBlock = (message: string) => (
	<>
		<p class="error">{message}</p>
		<p>
			<a href="/">Back to the Shabti Configurator</a>
		</p>
	</>
);

// the flag is how the watchdog below knows a verdict was reached at all
const finished = () => (
	<script
		dangerouslySetInnerHTML={{ __html: "window.shabtiFinished = true;" }}
	></script>
);

/**
 * A streamed document only fires load once the stream closes, so this catches every way the page can
 * end without a verdict: hono's stream() drops anything thrown which isn't an Error, its writes
 * swallow their own failures, and the server can simply die. All of those used to leave a silently
 * truncated page, which the meta refresh that used to live here turned into a clean trip back to the
 * main page, indistinguishable from a successful install.
 *
 * The message rides along as markup in a template rather than as a string inside the script, so
 * there's nothing to escape into script context and failureBlock stays the one definition of it.
 */
const watchdog = () => (
	<>
		<template id="shabti_interrupted">{failureBlock(INTERRUPTED)}</template>
		<script
			dangerouslySetInnerHTML={{
				__html: `window.shabtiFinished = false;addEventListener("load", () => {if (window.shabtiFinished) return;const template = document.getElementById("shabti_interrupted");if (template) document.body.append(template.content.cloneNode(true));});`,
			}}
		></script>
	</>
);

/**
 * The message travels in an attribute, which hono escapes, rather than interpolated into the script.
 * That leaves encodeURIComponent doing its actual job of building a query string in the browser,
 * instead of doubling as the guard which keeps the message inside its JS string literal.
 */
const success = (endMessage?: string) => (
	<>
		<div
			id="shabti_done"
			hidden
			data-message={endMessage || "Operation completed successfully."}
		></div>
		<script
			dangerouslySetInnerHTML={{
				__html: `window.shabtiFinished = true;const done = document.getElementById("shabti_done");if (done) window.location = "/?done=" + encodeURIComponent(done.dataset.message);`,
			}}
		></script>
	</>
);

export default (
	c: Context,
	startMessage: string,
	func: (stream: StreamingApi) => Promise<void>,
	endMessage?: string,
) => {
	// no onError argument: hono never calls it for `throw undefined`, and only console.errors
	// anything else which isn't an Error instance, which is exactly the case that has to be caught
	const resp = stream(c, async (stream) => {
		await stream.writeln(
			await (
				<>
					<head>
						<link rel="stylesheet" href="/style.css" />
						{watchdog()}
					</head>
					<p>{startMessage}</p>
				</>
			),
		);
		try {
			await func(stream);
		} catch (error) {
			await stream.writeln(await failureBlock(describeError(error)).toString());
			await stream.writeln(await finished().toString());
			return;
		}
		// only reachable by falling off the end of func, so nothing but a clean run reports success
		await stream.writeln("Done! returning to main page!");
		await stream.writeln(await success(endMessage).toString());
	});
	resp.headers.set("Content-Type", "text/html; charset=UTF-8");
	resp.headers.set("Transfer-Encoding", "chunked");
	return resp;
};
