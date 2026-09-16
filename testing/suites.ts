import path from "node:path";

/**
 * A test type is a set of suites that share a Shabti configuration, and therefore a stack.
 * Grouping this way is what keeps the run to at most one OpenSearch and Keycloak cold start per
 * configuration.
 */
export const TEST_TYPES = ["unit", "disabled", "enabled"] as const;

export type TestType = (typeof TEST_TYPES)[number];

/** what the report and the docs call each test type */
export const typeLabel: Record<TestType, string> = {
	unit: "unit",
	disabled: "security-disabled",
	enabled: "security-enabled",
};

/** the long form is what the env files and test directories are named, so accept it too */
export const typeAliases: Record<string, TestType> = {
	unit: "unit",
	disabled: "disabled",
	enabled: "enabled",
	"security-disabled": "disabled",
	"security-enabled": "enabled",
	security_disabled: "disabled",
	security_enabled: "enabled",
};

/** the test types that need a live Shabti stack, and so pay for the setup unit tests skip */
export const isEndToEnd = (testType: TestType) => testType !== "unit";

export interface Suite {
	/** what you type on the command line, and what the report calls it */
	id: string;
	testTypes: TestType[];
	/** the compose service the tests run in */
	service: string;
	runner: "pytest" | "bun";
	/** working directory inside the container, when the image's own default isn't the right one */
	workdir?: string;
	/**
	 * Talks to the running API rather than building the app in process. Mirrors the depends_on in
	 * e2e.yml, which cannot cover this on its own: `compose run` starts a dependency from whatever
	 * image is already there and never builds one.
	 */
	needsLiveApi?: boolean;
	/** what gets handed to the runner, relative to the working directory */
	target: (testType: TestType) => string;
	/** one line for --list */
	blurb: string;
}

const mode = (testType: TestType) =>
	testType === "enabled" ? "enabled" : "disabled";
const Mode = (testType: TestType) =>
	testType === "enabled" ? "Enabled" : "Disabled";

export const SUITES: Suite[] = [
	{
		id: "versioning",
		testTypes: ["unit"],
		service: "bun-tests",
		runner: "bun",
		target: () => "versioning/tests",
		blurb: "the release bumper, against throwaway git repos",
	},
	{
		id: "dependencies",
		testTypes: ["unit"],
		service: "bun-tests",
		runner: "bun",
		target: () => "dependencies/tests",
		blurb: "the dependency pin auditor, with every registry stubbed",
	},
	{
		id: "configurator",
		testTypes: ["unit"],
		service: "bun-tests",
		runner: "bun",
		// streamHtml.tsx needs shabti_configurator's own tsconfig for its jsxImportSource: the
		// repository root's has none, so from /repo the JSX would look for react/jsx-runtime
		workdir: "/repo/shabti_configurator",
		target: () => "tests",
		blurb:
			"the configurator's error reporting, with the stream read in process",
	},
	{
		id: "isi-util",
		testTypes: ["unit"],
		service: "pytest-api",
		runner: "pytest",
		workdir: "/app/python_packages/isi_util",
		target: () => "tests",
		blurb: "the async helpers in python_packages/isi_util",
	},
	{
		id: "api-unit",
		testTypes: ["unit"],
		service: "pytest-api",
		runner: "pytest",
		target: () => "tests/unit",
		blurb: "the API's tests that need no services at all",
	},
	{
		id: "api",
		testTypes: ["disabled", "enabled"],
		service: "pytest-api",
		runner: "pytest",
		target: (testType) => `tests/security_${mode(testType)}`,
		blurb: "the API, built in process against a live stack",
	},
	{
		id: "python-client",
		testTypes: ["disabled", "enabled"],
		needsLiveApi: true,
		service: "pytest-python-client",
		runner: "pytest",
		target: (testType) => `tests/security_${mode(testType)}`,
		blurb: "the Python client, over the network",
	},
	{
		id: "node-client",
		testTypes: ["disabled", "enabled"],
		needsLiveApi: true,
		service: "bun-tests",
		runner: "bun",
		target: (testType) =>
			`shabti_api_client_node/tests/shabtiClientSecurity${Mode(testType)}.test.ts`,
		blurb: "the Node client, over the network",
	},
	{
		id: "cli",
		testTypes: ["disabled", "enabled"],
		needsLiveApi: true,
		service: "bun-tests",
		runner: "bun",
		target: (testType) =>
			`shabti_cli/tests/shabtiCliSecurity${Mode(testType)}.test.ts`,
		blurb: "the CLI, driving the live API",
	},
];

export const suitesFor = (testType: TestType) =>
	SUITES.filter((suite) => suite.testTypes.includes(testType));

/** the JUnit file a suite writes, the same name inside the container and on the host */
export const resultFile = (testType: TestType, suite: Suite) =>
	`${typeLabel[testType]}_${suite.id}.xml`;

/**
 * The runner's own arguments, plus whatever the user typed after the suite name.
 *
 * Leading bare words narrow the suite: each is joined onto the suite's own path, which pytest reads
 * as a node id and bun as a path substring. That is the "filter an individual file" case. From the
 * first flag onwards everything is passed through verbatim, so a flag keeps its value and `-k` and
 * `-t` do what they always do.
 */
export const command = (testType: TestType, suite: Suite, extra: string[]) => {
	const firstFlag = extra.findIndex((arg) => arg.startsWith("-"));
	const cut = firstFlag === -1 ? extra.length : firstFlag;
	const positionals = extra.slice(0, cut);
	const flags = extra.slice(cut);
	const target = suite.target(testType);
	// a suite whose target is a single file is narrowed relative to its directory
	const base = target.endsWith(".test.ts")
		? path.posix.dirname(target)
		: target;
	const paths = positionals.length
		? positionals.map((arg) => path.posix.join(base, arg))
		: [target];
	const out = `/test_results/${resultFile(testType, suite)}`;

	return suite.runner === "pytest"
		? [
				"uv",
				"run",
				"pytest",
				...paths,
				`--junit-xml=${out}`,
				"-o",
				`junit_suite_name=${typeLabel[testType]}/${suite.id}`,
				// nothing allocates a pseudo-tty, so colour has to be asked for
				"--color=yes",
				...flags,
			]
		: [
				"bun",
				"test",
				...paths,
				"--reporter=junit",
				`--reporter-outfile=${out}`,
				...flags,
			];
};
