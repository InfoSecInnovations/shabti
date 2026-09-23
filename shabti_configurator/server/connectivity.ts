export type Connectivity = "online" | "huggingface-unreachable" | "offline";

const TIMEOUT_MS = 3000;
// long enough that one page render only pays for the check once
const CACHE_MS = 30_000;

// hosts that don't depend on Hugging Face, so we can tell Hugging Face being down apart from
// having no internet connection at all
const OTHER_HOSTS = [
	"https://api.github.com",
	"https://registry-1.docker.io/v2/",
];

// any response at all means the host is reachable, even an error status
const reachable = (url: string) =>
	fetch(url, { method: "HEAD", signal: AbortSignal.timeout(TIMEOUT_MS) }).then(
		() => true,
		() => false,
	);

const check = async (): Promise<Connectivity> => {
	// all at once so being offline costs one timeout rather than one per host
	const [huggingFace, ...others] = await Promise.all(
		["https://huggingface.co", ...OTHER_HOSTS].map(reachable),
	);
	if (huggingFace) return "online";
	return others.some(Boolean) ? "huggingface-unreachable" : "offline";
};

// the promise rather than the result, so concurrent callers share one check
let cached: { at: number; result: Promise<Connectivity> } | undefined;

export const getConnectivity = () => {
	if (!cached || Date.now() - cached.at > CACHE_MS)
		cached = { at: Date.now(), result: check() };
	return cached.result;
};

/** whether models can be downloaded, which is what most of the configurator cares about */
export const isOnline = async () => (await getConnectivity()) == "online";

export const resetConnectivity = () => {
	cached = undefined;
};
