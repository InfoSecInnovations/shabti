const FALLBACK =
	"An unknown error occurred, check the configurator's terminal output.";

// Bun's ShellError message is only ever "Failed with exit code N", the command's own stderr is the
// part that says what actually went wrong. Duck typed rather than `instanceof $.ShellError` so a
// test can hand one over without spawning a shell
const isShellError = (
	error: unknown,
): error is { exitCode: number; stderr?: { toString(): string } } =>
	!!error &&
	typeof error == "object" &&
	typeof (error as { exitCode?: unknown }).exitCode == "number" &&
	"stderr" in error;

/** the start of a message is the informative part */
const head = (text: string, max: number) =>
	text.length > max ? `${text.slice(0, max - 1)}…` : text;

/** the end of a command's output is the informative part */
const tail = (text: string, max: number) =>
	text.length > max ? `…${text.slice(-(max - 1))}` : text;

/**
 * The user facing description of anything that was thrown, including the values `instanceof Error`
 * misses. The full error always goes to the terminal, so the page can stay to one readable line.
 */
export default (error: unknown, maxLength = 2000) => {
	console.error(error);
	// checked before Error, ShellError extends it
	if (isShellError(error)) {
		const stderr = error.stderr?.toString().trim();
		return stderr
			? `Command failed with exit code ${error.exitCode}: ${tail(stderr, maxLength)}`
			: `Command failed with exit code ${error.exitCode}.`;
	}
	if (error instanceof Error)
		return head(error.message.trim(), maxLength) || FALLBACK;
	if (typeof error == "string")
		return head(error.trim(), maxLength) || FALLBACK;
	return FALLBACK;
};
