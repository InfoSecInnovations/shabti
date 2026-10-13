# Shabti AI #  
AI should be simple, safe, and amazing.  

[Home Page](https://shabti.ai)

A project by [InfoSec Innovations (ISI)](https://www.infosecinnovations.com/)

## TL;DR: ##
Shabti AI is an AI system that works ONLY with the data you feed it. Your data, your prompts, everything are local to your instance.

## More details ##
Shabti is a local and modular RAG framework still in alpha.  

Built with simplicy and security in mind, it has some features we love -- and hope you do too!
* The quick install method is so easy.
* Loading data: Upload documents or point to a URL, click the ingest button... and the data is there for your use.
* Tasks: you can change what the AI can do for you via dropdowns.

## Dependencies ##  

These versions are currently being used to develop Shabti, lower versions may work but are untested. See [Known Issues](#known-issues) for an older Docker Compose command which might work for you if you're unable to upgrade.

- **Docker >= 25.0.3** (currently the vector database and natural language response engine are running in docker containers). Check with `docker --version`.
- **Docker Compose >= 2.24.6** (while frequently installed with docker, sometimes it's not. Docker compose files are how the docker containers are setup). Check with `docker compose version`.  
   
Optional:  
If you want to use GPU acceleration for the local models (Shabti does NOT require this, but it will make responses and ingesting dramatically faster), you must have the 
NVIDIA drivers correctly setup and running. Shabti will not install or make any adjustments to your driver configuration.  

Note: if you want to use GPU acceleration on a Windows host, you must use WSL2.  
More details here:  
https://docs.docker.com/desktop/gpu/

Refer to the documentation from NVIDIA for information on how to do this for your OS.

## System Requirements ##

It is unlikely you'll be able to run Shabti with less than 16GB RAM.

If you're using Docker Desktop you need to make sure at least 8GB RAM is assigned to be used by containers. The default is half of the system memory, so if you have at least 16GB RAM and haven't modified your configuration it's likely you don't need to do anything.

You should perform the following system configuration steps according to your Operating System: https://opensearch.org/docs/latest/install-and-configure/install-opensearch/docker/#install-docker-and-docker-compose, otherwise you may not be able to run the OpenSearch container Shabti depends on.

## Setup

Shabti now has a visual configurator which we hope you will like a lot more than the previous text-based one. Just go to the release you wish to install and download the executable for your Operating System. Please let us know if you're using a different Operating System, it may be possible for us to make a build for it. You can also use the development version.

Launch the executable and visit the indicated address in your web browser. Select your desired options and click "Start Installation!".

Note that the installer launches all the requirements for you, you generally won't need to use the "Launch Shabti" button in the "Manage Shabti" section. Docker will keep everything running for you and you will be able to access Shabti without going via the Configurator once installed.

Once Shabti is installed, the "Manage Shabti" section also lets you change which language models are available without reinstalling. Adding a model downloads it, removing one leaves the downloaded files in place so you can add it back again quickly. Applying changes restarts the LLM service, as it only picks up model configuration changes on startup.

Shabti can also answer prompts with any OpenAI-compatible API, such as OpenAI, OpenRouter or a self-hosted server, alongside or instead of the local models. Add one in the "Model Provider" section when installing, or under "Model Providers" in the "Manage Shabti" section afterwards: give it a name, its base URL and API key, click "Test Connection", and choose which of its models to offer. Provider changes apply straight away, without restarting anything. See [MODELS.md](docs/user/MODELS.md#providers) for more.

A provider can embed your documents too. If every model you choose comes from a provider, Shabti runs without llama.cpp, and the GPU and model downloads aren't needed. See [MODELS.md](docs/user/MODELS.md#embeddings-from-a-provider).

### Windows SmartScreen

As we have just recently released the executable, Windows may not yet recognize it as being "safe". We would really appreciate it if you could run the app anyway, as this will allow us to increase trust with Microsoft and prevent future released from being blocked by SmartScreen.

If you see this popup:

![SmartScreen popup](smartscreen.png)

Please click "More info" which should reveal that the executable is in fact signed by InfoSec Innovations, LLC. If you do not see that it's signed by us, please do not proceed! In the case that our name does appear there, click "Run anyway" and you will be able to use Shabti and help us out a great deal, thank you!

![SmartScreen showing the app is signed by ISI](smartscreen-isi.png)

## Usage

Once you have completed the installation process, Shabti will be running on `localhost:15130` or the host and port you selected during setup. It can take a little while for the containers to be ready after install or relaunch, especially on the first launch as the API needs to pull the embeddings model. If you chose to enable security you will need to access it using `https://` at the start of the address.

## Using Security Features

As we are a cybersecurity company, security considerations are driving the development of Shabti. You can just run on a local machine without any security features if that suits your needs, but if you need an enterprise solution that can run LLM tools while having full control of the data being fed into them, Shabti's security features may well be a good fit for you! We have built our Identity and Access Management solution on top of Keycloak, which is a very flexible tool allowing you to connect to existing user accounts from the vast majority of providers. We provide an extensible Role-Based Access Control configuration so you can manage how your users are able to access the data ingested into Shabti.

See the [dedicated guide](docs/user/Security_and_RBAC_Features.md) about using Shabti with security enabled.

## Known Issues

### MacOS executable will not run

We do not yet have code signing configured for MacOS so it will not recognize Shabti as a legitimate application. It is possible to bypass Gatekeeper if you wish to run Shabti anyway, but we have found that certain MacOS versions don't appear to "officially" support doing this.

### Ingesting documents appears to hang for a while

We do not yet have a way to display progress when a document is being processed into text, only once the text is being fed into the database. For smaller documents you probably won't notice this delay much, but processing a document with a very large amount of text can take a long time, please be patient!

## Troubleshooting

### Shabti API is down

Check the Docker logs for the container called `shabti`. If you don't see a log that says "Application startup complete", keep waiting, it may still be loading the LLM models.

### Shabti Web UI is broken or blank

Perform an "Empty Cache and Hard Reload" on the page. Depending on your browser you may need to open the dev tools to do this.

### Other issues

Please create an issue [here](https://github.com/InfoSecInnovations/concierge/issues). Check the Docker logs of the containers inside the `shabti` compose stack for any errors or warnings and include those in your report. Also include any errors displayed in the Web UI or the console during the install process.

## Setup: development environment

### Dependencies

The ones listed at the top of this page, and:
- **Bun >= 1.3.1** - the configurator is written in JavaScript that depends on the Bun runtime. Bun allows us to effortlessly build the executable files for each Operating System.
- **uv >= 0.10.0, Python 3.13** - most of the Python code runs in Docker containers now, however we also use Python to configure the pre-commit hooks for formatting. We're using Python through uv as it makes it easier to manage the Python executables and virtual environments in our experience.

### Installation

git clone repo or extract zip. 

`cd shabti` go into the cloned project directory.

do `bun install` in the repository root. This will set up all the JavaScript projects.

`cd shabti_configurator` to go to the configurator directory.

`bun run dev_install` will launch the web server in dev mode which provides additional install and launch options compared to the distributed executable. Once the server is running go to http://localhost:3000 to see the configuration options. If you select the `local` version in the version dropdown the development environment will be configured to use the files on your machine to run Shabti, if you select another version it will pull the images from Docker Hub, essentially functioning like the compiled executable we provide with releases.

## Usage: development environment

Install using the instructions above making sure to install the `local` version.

The recommended way to edit the code that runs in Docker is to open each subdirectory of `docker_containers` in Visual Studio Code using the provided devcontainer configuration.

With the subdirectory open in VSCode, press F1 and search for `Dev Container: Rebuild and Reopen in Container`. Using the rebuild command ensures the correct configuration of Shabti is launched, if you have run Shabti anywhere outside of the devcontainer environment you can end up with a mismatch between the development configuration and whatever you were running before if you don't rebuild.

If you don't wish to use the VSCode and devcontainer setup you can use the Launch button from the configurator which will launch the code in Docker, mounting the appropriate directories so changes are reflected both on the host system and inside the container.

Both of these methods will reload the program when changes are made to the code files.

Make sure to read the [Contribution Guide](CONTRIBUTING.md) to find out more about coding style enforcement and commit etiquette!

## CLI ##

Shabti now ships with a standalone executable for the Command Line Interface and the syntax has been completely revamped. We will add proper documentation for this, but you can call the executable with the `-h` flag to see which commands exist and how to use them. The CLI executable must be in the same directory as the main executable and will only work after you've installed Shabti. The CLI currently uses an admin account if you're using security features, so use it wisely!

## Known Issues

- Startup of the Shabti API service can be quite slow because it has to download models on the first run. It should generally start up faster subsequent times as it will cache the model downloads.
- Code signing is not yet implemented for MacOS, the executable will be blocked without doing some "hacks" to make it work. Look up bypassing Gatekeeper on your MacOS version or use the development version of the configurator.

## Want to get involved? ##

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) then our [Contribution Guide](CONTRIBUTING.md).
