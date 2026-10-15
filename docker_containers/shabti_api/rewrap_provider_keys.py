from src.app.functionality.providers.rewrap import rewrap_all

# run by the configurator after it rotates the key users' provider keys are encrypted with
if __name__ == "__main__":
    rewrap_all()
