# Run the tournament runner locally (default `machineplay` entrypoint).
run:
    uv run machineplay

# Pull & restart the runner on the VPS (see deploy-machineplay-cli in malganis).
deploy:
    ssh root@machineplay.org deploy-machineplay-cli
