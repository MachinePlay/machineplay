# Run the tournament runner locally.
run:
    uv run machineplay runner

# Pull & restart the runner on the VPS (see deploy-machineplay-cli in malganis).
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

# Publish to PyPI from this machine (needs UV_PUBLISH_TOKEN). Normally CI does
# it instead: push a v* tag, see .github/workflows/publish.yml.
publish: build
    uv publish
