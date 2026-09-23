import type { Connectivity } from "./connectivity";

const NOTICES: Record<Connectivity, string | undefined> = {
	online: undefined,
	"huggingface-unreachable":
		"Couldn't reach Hugging Face, only downloaded models are listed.",
	offline:
		"No internet connection, only downloaded versions and models are listed.",
};

export const ConnectivityNotice = (props: { connectivity: Connectivity }) => {
	const notice = NOTICES[props.connectivity];
	return notice ? <p>{notice}</p> : undefined;
};
