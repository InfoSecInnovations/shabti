// `bun run --parallel` doesn't exit on Ctrl+C on Windows, so the client bundle watcher and the
// server are run from here instead, sharing this console so their output comes through as is
const procs = ["dev_client", "dev_server"].map((script) =>
	Bun.spawn(["bun", "run", script], {
		stdio: ["inherit", "inherit", "inherit"],
	}),
);
const stop = () => {
	for (const proc of procs) proc.kill();
	process.exit();
};
process.on("SIGINT", stop);
// one of them dying leaves the dev setup half working, so take the other down with it
await Promise.race(procs.map((proc) => proc.exited));
stop();
