#!/usr/bin/env bash
#
# vps_pull.sh — Phase B of docs/VPS_RECOVERY.md
#
# RUN THIS FROM YOUR LOCAL MACHINE, from the repo root — NOT while SSH'd into the VPS.
# (The recovery-results run failed because rsync/pg_dump were executed on the box itself.)
#
# Everything lands in vps_recovery/ which is gitignored. You'll be prompted for the VPS
# password once per command unless you install an SSH key first (see step 0).
#
set -euo pipefail

VPS="root@31.97.231.239"          # from recon
DEPLOY="/opt/cvolvepro"           # whole multi-app dir (CVOLVE-PRO lives under here)
OUT="vps_recovery"

cd "$(git rev-parse --show-toplevel)"   # ensure we're at repo root
mkdir -p "$OUT"

echo "==> 0. (optional, do once) install your SSH key so you stop typing the password:"
echo "    ssh-copy-id $VPS"
echo

echo "==> 1. Pull the CVOLVE-PRO deploy dir (code + secrets), skipping bulk we don't need"
rsync -avz \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='cvolvepro.sql' \
  "$VPS:$DEPLOY/CVOLVE-PRO/" "$OUT/CVOLVE-PRO/"
# ^ excludes the stale 191 MB cvolvepro.sql on purpose — we take a FRESH dump in step 5.
#   Keeps .env, .streamlit/secrets.toml, and CVOLVE-PRO-source.tar.gz (clean code snapshot).

echo "==> 2. Pull the Chrome extension source + signing keys (recon found these ON the VPS)"
# NOTE: these live at /opt level (siblings of cvolvepro/), NOT inside /opt/cvolvepro/.
rsync -avz "$VPS:/opt/cvolvepro-extension/"    "$OUT/cvolvepro-extension/"
rsync -avz "$VPS:/opt/cvolvepro-extension.crx" "$OUT/"
rsync -avz "$VPS:/opt/cvolvepro-keys/"         "$OUT/cvolvepro-keys/"

echo "==> 3. Pull the frontend + Stripe success page (payment-success.html lives here)"
rsync -avz "$VPS:$DEPLOY/frontend-interview/"   "$OUT/frontend-interview/"

echo "==> 4. Pull deployment config: nginx + systemd units"
mkdir -p "$OUT/nginx/sites-available" "$OUT/nginx/sites-enabled" "$OUT/systemd"
rsync -avz "$VPS:/etc/nginx/sites-available/"   "$OUT/nginx/sites-available/"
rsync -avz "$VPS:/etc/nginx/sites-enabled/"     "$OUT/nginx/sites-enabled/"
rsync -avz "$VPS:/etc/systemd/system/cvolvepro.service"     "$OUT/systemd/"
rsync -avz "$VPS:/etc/systemd/system/cvolvepro_api.service" "$OUT/systemd/"

echo "==> 5. Take a FRESH database dump (Postgres is local on the VPS)"
# Run pg_dump ON the VPS and stream it back to a local file. Prefer off-peak hours.
# `postgres` uses PEER auth over the socket, so `-U postgres` alone fails ("Peer
# authentication failed"). Since we log in as root, run the dump as the postgres OS user
# instead — peer auth then matches and no DB password is needed.
ssh "$VPS" "sudo -u postgres pg_dump cvolvepro" > "$OUT/cvolvepro_fresh_$(date +%Y%m%d).sql"

echo
echo "==> DONE. Recovered into $OUT/"
echo "    Next: copy secrets into local .env, then run the drift diff:"
echo "    diff -rq $OUT/CVOLVE-PRO/ . --exclude=.venv --exclude=.git \\"
echo "        --exclude=vps_recovery --exclude=__pycache__ --exclude='*.sql' --exclude=users.db"
