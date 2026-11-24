# Run the runner locally (direnv points it at the local stack).
run:
    uv run machineplay runner

# Pull & restart the runner on the VPS.
deploy:
    ssh root@machineplay.org deploy-machineplay-cli

# Follow runner logs from the VPS.
logs:
    ssh -t root@machineplay.org 'journalctl -u machineplay-runner -n 200 -f'

# Build the sdist + wheel into dist/ and check their PyPI metadata.
build:
    rm -rf dist
    uv build
    uvx twine check dist/*

# Publish to PyPI by hand (needs UV_PUBLISH_TOKEN); normally pushing a v* tag
# lets CI do it.
publish: build
    uv publish
