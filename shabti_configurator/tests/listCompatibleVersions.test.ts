import { mkdir } from "node:fs/promises";
import path from "node:path";
import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import * as octokit from "octokit";
import getVersionData from "../server/getVersionData";
import listCompatibleVersions, {
	getCompatibleReleases,
	getReleasesCachePath,
	resetCompatibleReleases,
} from "../server/listCompatibleVersions";
import { dockerState, json, mockDocker, mockFetch, useTempCwd } from "./mocks";

const release = (version: string, apiVersion: string) => ({
	configuratorMinVersion: "0.1.0",
	version,
	apiVersion,
	webVersion: apiVersion,
});

const RELEASES = [release("0.7.0", "0.0.9"), release("0.8.0", "0.1.0")];

const github = { fails: false, listings: 0 };
let docker: ReturnType<typeof dockerState>;
const cwd = useTempCwd();

const withImages = (tag: string) => {
	docker.images.add(`infosecinnovations/shabti:${tag}`);
	docker.images.add(`infosecinnovations/shabti-web:${tag}`);
};

beforeEach(async () => {
	await cwd.enter();
	github.fails = false;
	github.listings = 0;
	docker = dockerState();
	mockDocker(docker);
	spyOn(octokit, "Octokit").mockImplementation(function () {
		return {
			rest: { repos: { listReleases: {} } },
			paginate: async () => {
				github.listings++;
				if (github.fails) throw new TypeError("fetch failed");
				return RELEASES.map((_, i) => ({
					assets: [
						{ name: "shabti-components.json", url: `https://assets/${i}` },
					],
				}));
			},
		};
	} as any);
	mockFetch((url) =>
		url.startsWith("https://assets/")
			? json(RELEASES[Number(url.split("/").pop())])
			: undefined,
	);
});

afterEach(async () => {
	mock.restore();
	resetCompatibleReleases();
	await cwd.leave();
});

describe("getCompatibleReleases", () => {
	test("lists the releases newest first and caches them", async () => {
		expect(await listCompatibleVersions()).toEqual(["0.8.0", "0.7.0"]);
		expect(await Bun.file(getReleasesCachePath()).json()).toEqual(RELEASES);
	});

	test("reuses a successful listing", async () => {
		await listCompatibleVersions();
		await listCompatibleVersions();
		expect(github.listings).toBe(1);
	});

	test("offers the cached releases whose images are downloaded", async () => {
		await Bun.write(getReleasesCachePath(), JSON.stringify(RELEASES));
		github.fails = true;
		withImages("0.1.0");
		expect(await getCompatibleReleases()).toEqual({
			releases: [release("0.8.0", "0.1.0")],
			fromCache: true,
		});
	});

	test("offers the installed version without a cache", async () => {
		await mkdir("docker_compose");
		await Bun.write(
			path.join("docker_compose", ".env"),
			"SHABTI_VERSION=0.8.0\nSHABTI_API_VERSION=0.1.0\nSHABTI_WEB_VERSION=0.1.0\nSHABTI_LOCAL_VERSION=False\n",
		);
		github.fails = true;
		withImages("0.1.0");
		expect(await listCompatibleVersions()).toEqual(["0.8.0"]);
		// which is what keeps a reinstall on the images it already has
		expect((await getVersionData("0.8.0"))?.apiVersion).toBe("0.1.0");
	});

	test("offers nothing offline when nothing is downloaded", async () => {
		await Bun.write(getReleasesCachePath(), JSON.stringify(RELEASES));
		github.fails = true;
		expect(await listCompatibleVersions()).toEqual([]);
	});
});
