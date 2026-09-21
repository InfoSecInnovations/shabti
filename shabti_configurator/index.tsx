import { parseArgs } from "node:util";
import AdmZip from "adm-zip";
import { file } from "bun";
import { Hono } from "hono";
import js from "./assets/index.js" with { type: "file" };
import clientCss from "./assets/index.css" with { type: "file" };
import css from "./assets/style.css" with { type: "file" };
import doInstall from "./server/doInstall";
import doLaunch from "./server/doLaunch";
import doUninstall from "./server/doUninstall";
import dockerIsRunning from "./server/dockerIsRunning";
import { InstallOptionsForm } from "./server/installOptionsForm";
import manageModels from "./server/manageModels";
import { manageHostsAndPorts, manageLogging } from "./server/manageSettings";
import { ModelManagementForm } from "./server/modelManagementForm";
import { RelaunchForm } from "./server/relaunchForm";
import { SettingsManagementForm } from "./server/settingsManagementForm";
import streamHtml from "./server/streamHtml";
import { Tabs } from "./server/tabs";
import { UninstallForm } from "./server/uninstallForm";
import validateInstallForm from "./server/validateInstallForm";
import packageJson from "./package.json";
import getCurrentVersion from "./server/getCurrentVersion";
import listCompatibleVersions from "./server/listCompatibleVersions.js";
import currentIsLocal from "./server/currentIsLocal";
import describeError from "./server/describeError";
import installIsIncomplete from "./server/installIsIncomplete";

const { values } = parseArgs({
	args: Bun.argv,
	options: {
		"dev-mode": {
			type: "boolean",
		},
	},
	strict: true,
	allowPositionals: true,
});

const devMode = !!values["dev-mode"];

const app = new Hono();
// anything thrown before the response starts streaming, a malformed form body being the realistic
// one. Once a stream is open streamHtml owns its own errors and this is never reached
app.onError((err, c) =>
	c.redirect(`/?err=${encodeURIComponent(describeError(err, 400))}`),
);
const state: { watchProcess?: Bun.Subprocess } = {
	watchProcess: undefined,
};
const defaultVersion = (await listCompatibleVersions())?.[0];

app.get("/style.css", async (c) =>
	c.body(await file(css).text(), 201, {
		"Content-Type": "text/css",
	}),
);
// styles from the packages used by the client bundle, our own style.css is loaded after this
// one so it can override them
app.get("/index.css", async (c) =>
	c.body(await file(clientCss).text(), 201, {
		"Content-Type": "text/css",
	}),
);
app.get("/index.js", async (c) =>
	c.body(await file(js).text(), 201, {
		"Content-Type": "text/javascript",
	}),
);
app.get("/", async (c) => {
	const dockerStatus = await dockerIsRunning();
	const currentVersion = await getCurrentVersion();
	const isLocal = await currentIsLocal();
	const localIsRunning = !!state.watchProcess;
	const incomplete = await installIsIncomplete();
	return await c.html(
		<html>
			<head>
				<link rel="stylesheet" href="./index.css" />
				<link rel="stylesheet" href="./style.css" />
			</head>
			<body>
				<h1>Shabti Configurator</h1>
				<div id="form_errors" class="error"></div>
				<div id="form_success" class="success"></div>
				{incomplete ? (
					<p class="error">
						The last install didn't finish, so Shabti may not work. Install
						again to fix it.
					</p>
				) : undefined}
				<p>
					This is a utility to install and configure Shabti AI, a tool made by{" "}
					<a
						href="https://www.infosecinnovations.com/"
						target="_blank"
						rel="noreferrer"
					>
						InfoSec Innovations
					</a>
				</p>
				<p>
					Having trouble? Got suggestions for improving Shabti? Head over to our{" "}
					<a
						href="https://github.com/InfoSecInnovations/shabti"
						target="_blank"
						rel="noreferrer"
					>
						GitHub
					</a>
					!
				</p>
				{dockerStatus ? (
					<Tabs
						tabs={[
							...(currentVersion
								? [
										{
											id: "manage_tab",
											label: "Manage Shabti",
											content: (
												<>
													<p>Shabti appears to be configured on this system</p>
													{isLocal ? (
														<p>
															You are running the development version from local
															files
														</p>
													) : (
														<p>You are using version {currentVersion}</p>
													)}
													<RelaunchForm
														isLocal={isLocal}
														localIsRunning={localIsRunning}
													></RelaunchForm>
													<ModelManagementForm></ModelManagementForm>
													<SettingsManagementForm></SettingsManagementForm>
													<UninstallForm></UninstallForm>
												</>
											),
										},
									]
								: []),
							{
								id: "install_tab",
								label: "Install Shabti",
								content: (
									<InstallOptionsForm
										devMode={devMode}
										currentVersion={currentVersion}
									></InstallOptionsForm>
								),
							},
						]}
					></Tabs>
				) : (
					<section>
						<h3>Docker isn't running, please start it!</h3>
						<p>
							Docker is needed to install and run Shabti. We don't currently
							have a way to integrate it into the installer, so you will have to
							install it yourself.
						</p>
						<p>
							You can find install instructions here:{" "}
							<a href="https://docs.docker.com/engine/install/">
								https://docs.docker.com/engine/install/
							</a>
							. Please note that on Linux you have to follow the instructions
							very precisely otherwise you can end up installing Docker
							incorrectly!
						</p>
						<p>
							If you have already installed it, please launch Docker Desktop or
							start the daemon, refresh the page and you should see the Shabti
							installation options.
						</p>
					</section>
				)}
			</body>
			<script src="index.js"></script>
		</html>,
	);
});
app.post("/install", (c) =>
	c.req.formData().then((data) => {
		if (!validateInstallForm(data)) return c.redirect("/?err=invalid-form");
		return streamHtml(
			c,
			"Installing Shabti",
			async (stream) => {
				for await (const message of doInstall(
					data,
					data.get("version")!.toString(),
					defaultVersion,
					state,
				)) {
					await stream.writeln(await (<p>{message}</p>));
				}
			},
			"Shabti installed successfully.",
		);
	}),
);
app.post("/uninstall", (c) =>
	c.req.formData().then((data) =>
		streamHtml(
			c,
			"Uninstalling Shabti",
			async (stream) => {
				for await (const message of doUninstall(
					data.has("delete_models"),
					state,
				)) {
					await stream.writeln(await (<p>{message}</p>));
				}
			},
			"Shabti uninstalled successfully.",
		),
	),
);
app.post("/manage-models", (c) =>
	c.req.formData().then((data) =>
		streamHtml(
			c,
			"Updating language models",
			async (stream) => {
				for await (const message of manageModels(data)) {
					await stream.writeln(await (<p>{message}</p>));
				}
			},
			"Language models updated successfully.",
		),
	),
);
app.post("/manage-logging", (c) =>
	c.req.formData().then((data) =>
		streamHtml(
			c,
			"Updating logging settings",
			async (stream) => {
				for await (const message of manageLogging(data, state)) {
					await stream.writeln(await (<p>{message}</p>));
				}
			},
			"Logging settings updated successfully.",
		),
	),
);
app.post("/manage-hosts", (c) =>
	c.req.formData().then((data) =>
		streamHtml(
			c,
			"Updating hosts and ports",
			async (stream) => {
				for await (const message of manageHostsAndPorts(data, state)) {
					await stream.writeln(await (<p>{message}</p>));
				}
			},
			"Hosts and ports updated successfully.",
		),
	),
);
app.post("/launch", (c) =>
	c.req.formData().then((data) =>
		streamHtml(c, "Launching Shabti", async (stream) => {
			for await (const message of doLaunch(data, state)) {
				await stream.writeln(await (<p>{message}</p>));
			}
		}),
	),
);

console.log("Shabti Configurator");
console.log(`${packageJson.version}\n`);

if (!devMode) {
	const dockerComposeZip = await import("./assets/docker_compose.zip", {
		with: { type: "file" },
	}).then((file) => file.default);
	// we need the compose files to be available outside of the executable bundle so the shell can use them
	const buf = await file(dockerComposeZip).arrayBuffer();
	const zip = new AdmZip(Buffer.from(buf));
	zip.extractAllTo(".", true);
	console.log("Extracted docker compose files.\n");
}

Bun.serve({ ...app, idleTimeout: 0 });
console.log("visit http://localhost:3000 to install or manage Shabti");
