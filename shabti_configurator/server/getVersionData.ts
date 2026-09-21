import { Octokit } from "octokit";

export default async (version: string) => {
	const octokit = new Octokit();
	const release = await octokit.rest.repos
		.getReleaseByTag({
			owner: "InfoSecInnovations",
			repo: "shabti",
			tag: `shabti-v${version}`,
			headers: {
				"X-GitHub-Api-Version": "2026-03-10",
			},
		})
		.catch((err) => {
			// if (err.status == 404) return undefined;
			// throw err;
			// TODO: catch specific error when there's no internet connection in addition to 404
			return undefined;
		});
	if (!release) return undefined;
	const componentsAsset = release.data.assets.find(
		(asset) => asset.name == "shabti-components.json",
	);
	if (!componentsAsset) return undefined;
	return await fetch(componentsAsset.url, {
		headers: { Accept: "application/octet-stream" },
	}).then((res) => res.json() as any);
};
