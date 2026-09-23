// what the server and the operation page agree on. The client bundle imports this too, so it must stay
// free of imports of its own

export const INTERRUPTED =
	"The operation stopped before it finished, check the configurator's terminal output.";
export const BACK = "Back to the Shabti Configurator";
export const RETURNING = "Done! returning to main page!";

/** something ongoing, reported again with the same key each time it moves on */
export type ProgressUpdate = {
	key: string;
	label: string;
	done: number;
	/** 0 when the total isn't known yet */
	total: number;
	detail: string;
};

/** what an operation's generator yields: a one-off message, or an update to a progress bar */
export type OperationUpdate = string | ProgressUpdate;

export type Line =
	| { kind: "message"; text: string }
	| ({ kind: "progress" } & ProgressUpdate);

export type OperationStatus =
	| { status: "running" }
	| { status: "failed"; message: string }
	| { status: "done"; message: string };

/** the payload of a `line` event, the client stores it at `index`, replacing what was there */
export type LineEvent = { index: number; line: Line };

export type OperationEvent =
	| { event: "line"; data: LineEvent }
	| { event: "status"; data: OperationStatus };
