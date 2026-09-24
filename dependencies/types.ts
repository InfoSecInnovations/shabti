/**
 * The vocabulary both commands share. Kept in one file rather than beside the code that produces each
 * type, because the reading half and the registry half are meant to be replaceable independently and
 * this is the seam between them.
 */

/** system is the uv and Bun installed on this machine, which no file pins; see system.ts */
export const ECOSYSTEMS = ["python", "node", "docker", "system"] as const;
export type Ecosystem = (typeof ECOSYSTEMS)[number];

/** the file shapes a pin can be written in. compose and dockerfile are both the docker ecosystem */
export const KINDS = ["python", "node", "compose", "dockerfile"] as const;
export type Kind = (typeof KINDS)[number];

/** how tightly a specifier constrains the version, least precise first */
export const PRECISIONS = [
	"absent",
	"tag",
	"alias",
	"range",
	"compatible",
	"exact",
] as const;
export type Precision = (typeof PRECISIONS)[number];

/** an image reference split into the parts a rewrite has to put back */
export type ImageRef = {
	/** "quay.io", "ghcr.io", or null for Docker Hub, which is implied rather than written */
	registry: string | null;
	/** "postgres", "astral/uv", "keycloak/keycloak" - as written, before Docker Hub's library/ rule */
	repository: string;
	/** the whole tag, or null when the reference carries none */
	tag: string | null;
	/** a digest pin, which set must refuse rather than silently drop */
	digest: string | null;
};

/** one place one dependency is pinned */
export type Pin = {
	/** the dependency as this file spells it: fastapi, @types/bun, quay.io/keycloak/keycloak */
	name: string;
	/** what both commands match on: PEP 503 for python, byte exact for node, registry/repo for docker */
	id: string;
	ecosystem: Ecosystem;
	/** posix, relative to the repo root, so it can be handed straight to git */
	file: string;
	/** 1 based, for the report only; 0 when it could not be recovered */
	line: number;
	/** the specifier as written: "~=4.3.0", "^15.0.0", "latest", "18.3", "" */
	specifier: string;
	/**
	 * The text the rewriter looks for, exactly as the file spells it: the whole requirement for python,
	 * the specifier alone for node, the whole image reference for docker. Kept rather than rebuilt from
	 * the parts, because `Python_Lib == 0.1.0` and `Python_Lib==0.1.0` are the same requirement and only
	 * one of them is in the file.
	 */
	raw: string;
	/** the version the specifier names, or null when it names none */
	version: string | null;
	precision: Precision;
	/** PEP 508 extras, so a rewrite can put them back: ["standard"] for fastapi[standard] */
	extras: string[];
	/** a PEP 508 environment marker, kept verbatim */
	marker: string | null;
	/** where it was declared: "project.dependencies", "devDependencies", "services.tika.image", "FROM" */
	location: string;
	image?: ImageRef;
};

/** whether every occurrence of a dependency names the same version */
export const AGREEMENTS = ["agreed", "diverged", "partial"] as const;
export type Agreement = (typeof AGREEMENTS)[number];

/** every occurrence of one dependency, grouped */
export type Dependency = {
	id: string;
	ecosystem: Ecosystem;
	/** the spelling to show: the most common one, so PEP 503 folding never renames anything */
	name: string;
	occurrences: Pin[];
	/** the distinct non-null versions; one entry means they agree */
	versions: string[];
	agreement: Agreement;
	/** the least precise precision among the occurrences: what a warning would be about */
	precision: Precision;
	/** docker only, and shared by every occurrence because the id is derived from it */
	image?: Omit<ImageRef, "tag" | "digest">;
	/** system only: the executable on PATH, absent when the tool is not installed */
	executable?: string;
};

export type Release = {
	/** canonical, i.e. what a manifest should be written with */
	version: string;
	/** as the registry spells it, present only when it differs from `version` */
	raw?: string;
	prerelease: boolean;
	/** why it should not be moved to: "yanked" on PyPI, the deprecation message on npm */
	withdrawn?: string;
};

export type Catalogue = {
	/** every release the registry lists, ascending */
	releases: Release[];
	/** highest non-prerelease, non-withdrawn release */
	latestStable?: Release;
	/** highest prerelease, reported only when it sorts above latestStable */
	latestPrerelease?: Release;
	/** the registry's own pointers: npm dist-tags, or what a docker `latest` tag resolves to */
	distTags?: Record<string, string>;
	/** docker only: the literal held constant while searching, e.g. "-python3.14-trixie-slim" */
	label?: string;
	/** non-fatal observations, carried by --json rather than by the table */
	notes: string[];
};

export type Verdict = {
	behind: boolean;
	/** how many stable releases lie between the pin and latestStable */
	behindBy?: number;
	/** the pin is not in the catalogue at all: renamed, unpublished, or a typo */
	unknownPin?: boolean;
	/** the pin itself is yanked or deprecated */
	pinWithdrawn?: string;
};

export type Freshness =
	| {
			status: "ok";
			dependency: Dependency;
			catalogue: Catalogue;
			verdict: Verdict;
	  }
	| { status: "unsupported"; dependency: Dependency; reason: string }
	| { status: "failed"; dependency: Dependency; reason: string };
