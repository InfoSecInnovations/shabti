import listCompatibleVersions from "./listCompatibleVersions";

export const VersionSelector = async (props: {
	id: string;
	devMode: boolean;
	currentVersion?: string;
}) => {
	const availableVersions = await listCompatibleVersions();
	return (
		<select id={props.id} name="version">
			{props.devMode && <option value="local">local</option>}
			{availableVersions.map((version) => (
				<option value={version} selected={version == props.currentVersion}>
					{version}
				</option>
			))}
		</select>
	);
};
