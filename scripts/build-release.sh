#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^VERSION = "\([0-9][0-9.]*\)"$/\1/p' "$ROOT_DIR/hub/app.py")"
AGENT_VERSION="$(sed -n 's/^VERSION = "\([0-9][0-9.]*\)"$/\1/p' "$ROOT_DIR/agent/agent.py")"
if [[ -z "$VERSION" || "$VERSION" != "$AGENT_VERSION" ]]; then
  echo "Hub and Agent versions are missing or different."
  exit 1
fi

DIST_DIR="$ROOT_DIR/dist"
STAGE_DIR="$(mktemp -d /tmp/dark-noc-release.XXXXXX)"
trap 'rm -rf "$STAGE_DIR"' EXIT
mkdir -p "$DIST_DIR" "$STAGE_DIR/dark-noc-pro" "$STAGE_DIR/dark-noc-node"

cp -a "$ROOT_DIR/hub" "$ROOT_DIR/agent" "$ROOT_DIR/deploy" "$ROOT_DIR/tests" "$STAGE_DIR/dark-noc-pro/"
cp "$ROOT_DIR/install-hub.sh" "$ROOT_DIR/upgrade.sh" "$ROOT_DIR/uninstall.sh" \
  "$ROOT_DIR/README.md" "$ROOT_DIR/README.fa.md" "$ROOT_DIR/LICENSE" \
  "$ROOT_DIR/CHANGELOG.md" "$ROOT_DIR/SECURITY.md" "$ROOT_DIR/SUPPORT.md" "$STAGE_DIR/dark-noc-pro/"

mkdir -p "$STAGE_DIR/dark-noc-node/agent" "$STAGE_DIR/dark-noc-node/deploy"
cp -a "$ROOT_DIR/agent/." "$STAGE_DIR/dark-noc-node/agent/"
cp "$ROOT_DIR/deploy/dark-noc-agent.service" "$STAGE_DIR/dark-noc-node/deploy/"
cp "$ROOT_DIR/install-node.sh" "$ROOT_DIR/install-agent.sh" "$ROOT_DIR/NODE-README.md" \
  "$ROOT_DIR/LICENSE" "$STAGE_DIR/dark-noc-node/"

tar --exclude='__pycache__' --exclude='*.pyc' -czf "$DIST_DIR/DARK-NOC-HUB-v$VERSION.tar.gz" -C "$STAGE_DIR" dark-noc-pro
tar --exclude='__pycache__' --exclude='*.pyc' -czf "$DIST_DIR/DARK-NOC-NODE-v$VERSION.tar.gz" -C "$STAGE_DIR" dark-noc-node
(
  cd "$DIST_DIR"
  sha256sum "DARK-NOC-HUB-v$VERSION.tar.gz" "DARK-NOC-NODE-v$VERSION.tar.gz" > SHA256SUMS
)
echo "Release v$VERSION created in $DIST_DIR"
