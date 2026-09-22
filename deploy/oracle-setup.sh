#!/usr/bin/env bash
# Bootstrap the GeM ingest on an Oracle Cloud Always Free VM.
#
# Run once, on the VM, as the default user (ubuntu). Idempotent: running it
# again updates the checkout, rebuilds, and leaves the timer as it was.
#
#   curl -fsSL https://raw.githubusercontent.com/kansalakshat/Tender-os/main/deploy/oracle-setup.sh | bash
#
# Shape: VM.Standard.A1.Flex, 4 OCPU / 24 GB, Ubuntu 24.04 (arm64). The image
# this builds on is multi-arch, so arm64 is fine -- verified against the
# registry manifest, not assumed. The 1 GB AMD micro shape is NOT enough:
# Chromium needs room, and the OOM killer is how you find that out.
#
# No inbound ports are opened and none are needed. This host only makes
# outbound connections, to GeM and to the database.
set -euo pipefail

REPO=https://github.com/kansalakshat/Tender-os.git
DIR="$HOME/tender"
ENV_FILE=/etc/tender-ingest.env
IMAGE=tender-ingest

say() { printf '\n== %s\n' "$*"; }

say "Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

say "Source"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone "$REPO" "$DIR"; fi

say "Secrets"
# Kept out of the image and out of the repo: the image is rebuilt often and a
# baked-in database URL would end up in every layer of it.
if [ ! -f "$ENV_FILE" ]; then
  read -rp  "DATABASE_URL (from .env.production): " DB
  read -rp  "CONTACT_EMAIL: " EMAIL
  printf 'DATABASE_URL=%s\nCONTACT_EMAIL=%s\nRETENTION_DAYS=0\n' "$DB" "$EMAIL" | sudo tee "$ENV_FILE" >/dev/null
  sudo chmod 600 "$ENV_FILE"
  echo "wrote $ENV_FILE"
else
  echo "$ENV_FILE exists, leaving it alone"
fi

say "Build"
sudo docker build -t "$IMAGE" "$DIR"

say "Schedule"
# A service plus a timer, not a cron line: systemd keeps the exit status and the
# logs, so a run that fails leaves something to read. Type=oneshot because this
# runs a cycle and exits -- see the Dockerfile.
sudo tee /etc/systemd/system/tender-ingest.service >/dev/null <<UNIT
[Unit]
Description=Tender ingest (one cycle)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
# --rm so a daily run does not leave a year of dead containers behind.
ExecStart=/usr/bin/docker run --rm --env-file ${ENV_FILE} ${IMAGE}
TimeoutStartSec=4h
UNIT

sudo tee /etc/systemd/system/tender-ingest.timer >/dev/null <<'UNIT'
[Unit]
Description=Run the tender ingest daily

[Timer]
OnCalendar=*-*-* 01:30:00 UTC
# The VM should not be off, but if it ever is, run on the next boot rather than
# skipping the day. GeM bids close in about two weeks; a missed day is a hole.
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now tender-ingest.timer

say "Done"
systemctl list-timers tender-ingest.timer --no-pager || true
cat <<'NOTE'

Run it now (does not wait for the timer):
    sudo systemctl start tender-ingest.service

Watch it:
    journalctl -u tender-ingest.service -f

Last run's outcome:
    systemctl status tender-ingest.service
NOTE
