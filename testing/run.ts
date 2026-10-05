import { Command } from "commander";
import {
	type SuiteRun,
	archive,
	clearResults,
	readCases,
	render,
} from "./report";
import prepareSecurity, { loadEnvFor, reuseSecurity } from "./security";
import {
	addKeycloakDemoUsers,
	build,
	down,
	lockPythonDeps,
	nuke,
	preloadModels,
	runSuite,
	writeTestModelsIni,
} from "./stack";
import {
	TEST_TYPES,
	SUITES,
	type TestType,
	isEndToEnd,
	typeAliases,
	typeLabel,
	suitesFor,
} from "./suites";

const program = new Command()
	.name("test")
	.description(
		"Runs every Shabti test suite. The test types run in the order unit,\n" +
			"security-disabled, security-enabled, and only the ones that need a stack start one.\n\n" +
			"Runner flags go before the test type. After the suite name, a bare word narrows\n" +
			"the suite to a file, and from the first flag onwards everything is passed to pytest\n" +
			"or bun verbatim, so -k and -t work as they always do.",
	)
	.argument(
		"[type]",
		"unit | disabled | enabled, or a suite to run in every type it has",
	)
	.argument("[suite]", "a single suite within the test type")
	.argument(
		"[filters...]",
		"a file to narrow to, then anything pytest or bun should see",
	)
	.option("-l, --list", "show the test types and suites, then exit")
	.option(
		"--keep-up",
		"leave the stack running afterwards; pair with --no-clean to actually reuse it",
	)
	.option("--no-clean", "keep the existing OpenSearch and Keycloak state")
	.option("--no-lock", "skip the Python lockfile refresh")
	.option("--bail", "stop the whole run at the first failing suite")
	.option(
		"--timeout <minutes>",
		"wall clock limit per suite, so a hung test fails instead of stalling",
	)
	// so that anything after the test type and suite is treated as an operand and reaches the
	// underlying runner untouched
	.enablePositionalOptions()
	.passThroughOptions()
	.allowUnknownOption()
	.parse();

const opts = program.opts();

if (opts.list) {
	for (const testType of TEST_TYPES) {
		console.log(`\n  ${typeLabel[testType]}`);
		for (const suite of suitesFor(testType))
			console.log(`    ${suite.id.padEnd(15)}${suite.blurb}`);
	}
	console.log(
		"\n  bun run test                       everything" +
			"\n  bun run test unit                  one test type" +
			"\n  bun run test unit versioning       one suite" +
			"\n  bun run test api                   one suite in every type it belongs to" +
			"\n  bun run test disabled api test_collections_api.py::test_create" +
			"\n  bun run test enabled cli -t upload\n",
	);
	process.exit(0);
}

const [first, second, ...filters] = program.args;

/**
 * The first word is a test type, or a suite on its own -- which runs it in every type it belongs
 * to. Anything left over is a filter for the runner.
 */
const select = () => {
	if (!first)
		return { testTypes: [...TEST_TYPES] as TestType[], suiteIds: undefined };
	const testType = typeAliases[first];
	if (testType)
		return { testTypes: [testType], suiteIds: second ? [second] : undefined };
	const suite = SUITES.find((candidate) => candidate.id === first);
	if (suite) {
		if (second) filters.unshift(second);
		return { testTypes: suite.testTypes, suiteIds: [suite.id] };
	}
	console.error(
		`unknown test type or suite "${first}". Run with --list to see what there is.`,
	);
	process.exit(2);
};

const { testTypes, suiteIds } = select();

if (
	suiteIds &&
	!testTypes.some((testType) =>
		suitesFor(testType).some((suite) => suiteIds.includes(suite.id)),
	)
) {
	console.error(
		`"${suiteIds[0]}" is not a suite in the ${testTypes.map((name) => typeLabel[name]).join(" or ")} test type. Run with --list to see what there is.`,
	);
	process.exit(2);
}

const timeoutMs = (Number(opts.timeout) || 0) * 60_000;
const typeTimeout = (testType: TestType) =>
	timeoutMs || (isEndToEnd(testType) ? 60 * 60_000 : 10 * 60_000);

const runs: SuiteRun[] = [];
const typesRun: TestType[] = [];
let bailed = false;

await clearResults();

// only pytest suites need the lockfiles, and once per invocation is enough -- the old harness
// regenerated them twice, once per phase
const needsLock =
	opts.lock &&
	testTypes.some((testType) =>
		suitesFor(testType).some(
			(suite) =>
				suite.runner === "pytest" && (!suiteIds || suiteIds.includes(suite.id)),
		),
	);
if (needsLock) {
	console.log("updating Python lockfiles...");
	const locked = await lockPythonDeps().nothrow();
	if (locked.exitCode !== 0) {
		console.error(
			'could not update the Python lockfiles. On Linux set UV_LOCK_USER to "$(id -u):$(id -g)" so they are not written as root, or pass --no-lock to skip this.',
		);
		process.exit(1);
	}
}

for (const testType of testTypes) {
	if (bailed) break;
	const inType = suitesFor(testType);
	const selected = suiteIds
		? inType.filter((suite) => suiteIds.includes(suite.id))
		: inType;
	// a test type with nothing selected never starts its stack
	if (!selected.length) continue;

	typesRun.push(testType);
	console.log(`
-----------------------------------------------------
  ${typeLabel[testType].toUpperCase()}
-----------------------------------------------------
`);

	const ran = new Set<string>();
	/** whatever a test type did not get to still has to appear in the report */
	const account = (note?: string, tail?: string[]) => {
		for (const suite of inType) {
			if (ran.has(suite.id)) continue;
			const wanted = selected.includes(suite) && !bailed;
			runs.push({
				testType,
				suite,
				outcome: wanted && note ? "failed" : undefined,
				seconds: 0,
				cases: [],
				note: wanted ? note : undefined,
				tail: wanted && note ? tail : undefined,
			});
		}
	};

	try {
		if (isEndToEnd(testType)) {
			loadEnvFor(testType === "enabled" ? "enabled" : "disabled");
			await writeTestModelsIni();
			if (opts.clean) await nuke(testType);
			if (testType === "enabled")
				await (opts.clean ? prepareSecurity() : reuseSecurity());
		}

		console.log(
			"building... (can take some time if there are updates to the dependencies)",
		);
		// `compose run` starts a depends_on service from whatever image is already there, so the
		// API has to be built alongside the runners or a suite would test a stale one
		const services = [
			...new Set([
				...selected.map((suite) => suite.service),
				...(selected.some((suite) => suite.needsLiveApi) ? ["shabti"] : []),
			]),
		];
		const built = await build(testType, services);
		if (built.code !== 0) {
			// accounted for rather than thrown, so the build output survives into the summary
			account("the images could not be built", built.tail);
			continue;
		}

		// the realm already has them if its state was kept, and create_user rejects duplicates
		if (testType === "enabled" && opts.clean) await addKeycloakDemoUsers();

		if (isEndToEnd(testType)) {
			console.log("downloading the test models...");
			const preloaded = await preloadModels(testType);
			if (preloaded.code !== 0) {
				account("the test models could not be downloaded", preloaded.tail);
				continue;
			}
		}

		for (const suite of selected) {
			if (bailed) break;
			console.log(`
____________ ${typeLabel[testType]} / ${suite.id} ______________
`);
			const result = await runSuite(
				testType,
				suite,
				filters,
				typeTimeout(testType),
			);
			const cases = (await readCases(testType, suite)) ?? [];
			const failed =
				result.code !== 0 || cases.some((item) => item.status === "failed");
			ran.add(suite.id);
			runs.push({
				testType,
				suite,
				outcome: result.timedOut ? "timed out" : failed ? "failed" : "passed",
				seconds: result.seconds,
				cases,
				note:
					failed && !cases.length
						? `exited ${result.code} without writing any results`
						: undefined,
				// kept for any failure, not just one with no cases: bun's JUnit reporter can write a
				// <failure> with no message at all, and then this is the only reason there is
				tail: failed ? result.tail : undefined,
			});
			if (failed && opts.bail) bailed = true;
		}
		account();
	} catch (error) {
		// one test type falling over must not cost us the report, or the exit code
		console.error(`\n${typeLabel[testType]}: ${error}`);
		account(String(error instanceof Error ? error.message : error));
	} finally {
		if (!opts.keepUp) {
			console.log("tearing down...");
			await down(testType);
		}
	}
}

if (bailed) console.log("\nstopped early because --bail was given.");
// render prints the archive path itself, so that the failure detail stays the last thing on screen
render(runs, typesRun, await archive());

process.exitCode = runs.some(
	(run) => run.outcome && run.outcome !== "passed" && run.outcome !== "skipped",
)
	? 1
	: 0;
