/**
 * The whole network surface, in one place.
 *
 * Nothing here knows about registries. It exists to make three guarantees the registry modules would
 * otherwise each have to make for themselves: a dependency pinned in four files is fetched once, a
 * transient failure is retried, and the container registries - the only hosts with a real anonymous
 * rate limit - are never asked two things at the same time.
 */

export type Options = {
	/** total parallel requests */
	concurrency?: number;
	/** attempts after the first, on a network error, a 429 or a 5xx */
	retries?: number;
};

export type Client = {
	/**
	 * One request, retried and globally limited. `serial` names a host to serialise on: every request
	 * sharing a name waits for the previous one, which is what keeps the container registries to one
	 * request at a time.
	 */
	request: (
		url: string,
		init?: RequestInit,
		serial?: string,
	) => Promise<Response>;
	/** the result of `task`, computed once per key however many callers ask */
	once: <T>(key: string, task: () => Promise<T>) => Promise<T>;
};

/**
 * A slot is handed straight from a finishing task to the next waiting one rather than freed and
 * reclaimed, so a caller arriving in between cannot slip past the limit.
 */
const semaphore = (limit: number) => {
	let active = 0;
	const waiting: (() => void)[] = [];
	return async <T>(task: () => Promise<T>): Promise<T> => {
		if (active >= limit)
			await new Promise<void>((resolve) => waiting.push(resolve));
		else active++;
		try {
			return await task();
		} finally {
			const next = waiting.shift();
			if (next) next();
			else active--;
		}
	};
};

/** one chain per name, so tasks sharing a name run in order and never overlap */
const serialiser = () => {
	const chains = new Map<string, Promise<unknown>>();
	return <T>(name: string, task: () => Promise<T>): Promise<T> => {
		const next = (chains.get(name) ?? Promise.resolve()).then(task, task);
		// the chain must not carry a rejection forward, or one failure would fail every later request
		chains.set(
			name,
			next.then(
				() => undefined,
				() => undefined,
			),
		);
		return next;
	};
};

/** a 429 is worth waiting out and a 5xx is worth one more try; every other 4xx will not change */
const retryable = (status: number) => status === 429 || status >= 500;

/** the server's own answer to how long to wait, in seconds or as a date, when it gave one */
const retryAfter = (response: Response) => {
	const header = response.headers.get("retry-after");
	if (!header) return undefined;
	const seconds = Number(header);
	if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
	const date = Date.parse(header);
	return Number.isNaN(date) ? undefined : Math.max(0, date - Date.now());
};

const BACKOFF = [500, 1500];

export const client = (options: Options = {}): Client => {
	// completed and in-flight lookups, so the same dependency is never fetched twice
	const memo = new Map<string, Promise<unknown>>();
	const limit = semaphore(options.concurrency ?? 6);
	const serial = serialiser();
	const retries = options.retries ?? BACKOFF.length;

	const attempt = async (url: string, init?: RequestInit) => {
		let last: unknown;
		for (let tries = 0; tries <= retries; tries++) {
			if (tries) {
				const wait = BACKOFF[Math.min(tries - 1, BACKOFF.length - 1)] as number;
				await Bun.sleep(
					last instanceof Response
						? (retryAfter(last) ?? wait)
						: // a little jitter, so a whole run of dependencies does not retry in lockstep
							wait + (url.length % 100) * 5,
				);
			}
			try {
				const response = await fetch(url, init);
				if (!retryable(response.status)) return response;
				// the body is never read on a retryable status, so let the connection go
				await response.body?.cancel().catch(() => undefined);
				last = response;
			} catch (error) {
				last = error;
			}
		}
		if (last instanceof Response) return last;
		throw last;
	};

	return {
		request: (url, init, host) =>
			host
				? serial(host, () => limit(() => attempt(url, init)))
				: limit(() => attempt(url, init)),
		once: <T>(key: string, task: () => Promise<T>) => {
			const cached = memo.get(key);
			if (cached) return cached as Promise<T>;
			const running = task();
			memo.set(key, running);
			return running;
		},
	};
};
