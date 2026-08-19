#!/usr/bin/env bash
# One-shot deploy script for the CT Keno dashboard on an Oracle Cloud (or any
# Ubuntu) VM. Run as root or with sudo. Assumes the repo is already at
# /home/ubuntu/ct-keno-sim (scp'd or git-cloned).
set -euo pipefail

APP_DIR="/home/ubuntu/ct-keno-sim"
SERVICE="keno.service"

echo "==> Installing systemd service..."
cp "$APP_DIR/deploy/$SERVICE" "/etc/systemd/system/$SERVICE"

echo "==> Reloading systemd + enabling service..."
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo "==> Status:"
systemctl --no-pager status "$SERVICE" | head -n 12

echo ""
echo "Done. Dashboard is live at:  http://<YOUR-VM-IP>:8000"
echo "Today-only page:             http://<YOUR-VM-IP>:8000/today"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status keno     # check it's running"
echo "  sudo journalctl -u keno -f     # follow the scraper log"
echo "  sudo systemctl restart keno    # restart after code changes"
