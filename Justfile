# Run the tournament runner locally (default `machineplay` entrypoint).
run:
    uv run machineplay

# Pull & restart the runner on the VPS (see deploy-machineplay-cli in malganis).
deploy:
    ssh root@machineplay.org deploy-machineplay-cli

# Follow runner logs from the VPS.
logs:
    ssh -t root@machineplay.org 'journalctl -u machineplay-runner -n 200 -f'
