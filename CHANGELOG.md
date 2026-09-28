# Changelog

## 0.9.0 - TBD

### Changes

- Chat and embeddings models can be selected from a list of available models at install time
- Multiple chat models can be installed, in which case the model can be switched in the web app and API
- Document ingestion runs in parallel and has performance and UI improvements
- Web crawler switched to Crawlee due to the langchain community package being deprecated
- Duplicate documents are rejected and duplicate vectors are excluded when querying
- Configurator has been split into a panel to manage existing installs and a panel to install
- Better progress reporting during the install process
- Better support for running the configurator offline
- Apache Tika dependency upgraded to version 4
- Shiny for Python dependency upgraded to 1.7, this has enabled us to overcome some blockers caused by the previous Shiny version

### Fixes

- Zip file extraction issues
- Document filtering
- Prompting failure with logging enabled
- Better error reporting during configurator operations (sometimes it would report a successful install in spite of an error)
- Various stability issues
- Improved handling for queries which overflow the embeddings route's token limit

## 0.8.0 - 2026-07-28

Due to having several tasks ongoing simultaneously we've been unable to release the previous versions, but you can finally get your hands on a greatly upgraded version of Shabti (previously known as Concierge)! See also 0.7.0 changes below for the full list since the last official release.

### Changes

- The project is now called Shabti
- Added Apache Tika document processing solution to Shabti's dependencies. This allows you to load many types of document out of the box including Microsoft Office and open document formats, but also images of text using its built-in OCR capabilities!
- Switched from Ollama to Llama.cpp model runner. This offers a small increase in performance for prompting and allows us to generate embeddings much faster than Ollama could.
- The above changes have allowed us to remove various dependencies from the Shabti API project including PyTorch, this makes for a much much smaller Docker image and removes the need for separate CPU and cuda versions of Shabti. Note that the GPU accleration setting still exists but now only applies to the model runner container and not the Shabti API container.
- The development environment has been reworked to only support running the code within Docker in order to replicate the production environment more precisely. We have provided VSCode devcontainer configurations to enable developing directly inside the containers.
- Document metadata has been streamlined into a single format, this greatly simplifies the schema and queries in OpenSearch and makes it much simpler to handle multiple types of document.
- We've switched Python tooling from pip to uv which offers some additional features and hopefully more robustness in resolving dependencies and publishing packages.
- Documents can be ingested from a zip file.
- Much more extensive automated tests.

### Known Issues

- Code signing is not yet implemented for MacOS, the executable will be blocked without doing some "hacks" to make it work.

## 0.7.0 - rolled into 0.8.0 release

### Changes

- RBAC is here. Shabti can optionally be configured to use Keycloak to handle user accounts and permissions. You can keep using it without user accounts if you're just using it for personal use on a local machine. We imagine RBAC to be useful in an enterprise setting with multiple users being able to access the instance. Please let us know if you like this feature, and what additional controls we could ship with it.
- The installer script has been replaced by an executable file available for Windows, Linux and MacOS. This executable serves up a web UI to enable you to configure Shabti locally or remotely. This should make it a lot easier to get Shabti up and running, we felt that the text based installer was no longer practical and it was running into issues with different OSes and Python versions.
- The backend code has been moved to a REST API so you can integrate Shabti into your own stack or build your own front end if you wish. We have provided Python and Node.js client packages to provide a more streamlined experience calling the API.
- The CLI has been revamped and now calls the REST API.
- The base Docker image has been switched to the Python slim image which has much fewer known vulnerabilities than the full Python image and reduces the image size slightly, although we're aware that it's still quite large.
- Implemented JSON logging to a file. Logging can be enabled or disabled when setting up Shabti.
- Still a work in progress but some automated tests are now present. This will help us catch bugs more easily in the various permutations of Shabti that are possible.

### Fixes

- URL input list has been replaced with a custom component which should be much more robust.
- Fixed an issue where newly inserted documents could be missing from the web UI, sometimes even causing errors.

## 0.4.0 - 2024-09-05

### Changes

- OpenSearch backend now uses multiple indices to store document metadata, this avoids unnecessary duplication of said metadata and allows us to more easily reference specific documents in the collection.
- Documents are inserted into OpenSearch database instead of uploads directory.
- Some of the CLI scripts have been renamed to be more consistent and better fit the new database schema.
- Volumes are managed by Docker instead of mounting directories from the host OS. This allows us to resolve some issues with permissions that may have been preventing Linux users from using the app.
- Chat UI uses new chatbot interface provided by Shiny for Python.

### Added

- Install script makes necessary changes if previously installed version is incompatible with the one being installed. As we approach 1.0 we will refine this system to minimize data loss when upgrading, while still in Alpha phase we make no such promises!

## 0.3.0 - 2024-07-03

### Changes

- Vector Database backend now uses OpenSearch instead of Milvus. This will give us many more options with managing metadata from the ingested documents as well as strong RBAC features.
- install scripts should be a little more intuitive to use now.
- Loader page has been merged into Collection Management.
- The production version of Concierge now runs entirely in Docker, this should limit the number of issues relating to Python configurations.
- The user no longer needs to download the repository from GitHub to install Concierge, it is now done via the PyPI package manager.
- Default port set to 15130

### Added

- `delete_index` command
- `documents` command
- `list_indices` command
- `delete_document` command
- Ability to view documents in collection
- Ability to delete documents from collection
- Ability to set web app port in environment variables

## 0.2.0 - 2024-05-15

### Changes

- Web GUI ported from Streamlit to Shiny for Python: we have found Shiny to be much more robust, especially with handling long tasks such as ingesting documents.
- `launch.py` and `install.py` improved to (hopefully) work on more Operating Systems.

### Added

- Theme selector for Web GUI

## 0.1.3 - 2024-03-14

### Added

- `launch.py` script to make starting Concierge simpler.
- Collaborator Guide to clarify our internal policy on handling branches and versioning.

## 0.1.0 2024-03-06

Initial Data Concierge MVP release for public testing.

### Features

- `install.py` helper to ensure correct configuration
- Ability to ingest PDF documents and web pages into a Milvus vector database
- Ability to retrieve documents from the database that match the user's prompt
- Ability to answer the user's prompt using the retrieved documents as the context and Ollama to generate the response
- Streamlit powered Web UI
- CLI scripts also available


