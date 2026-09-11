#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
export TZ=UTC
umask 022

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^VERSION = "\([0-9][0-9.]*\)"$/\1/p' "$ROOT_DIR/hub/app.py")"
AGENT_VERSION="$(sed -n 's/^VERSION = "\([0-9][0-9.]*\)"$/\1/p' "$ROOT_DIR/agent/agent.py")"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || "$VERSION" != "$AGENT_VERSION" ]]; then
  echo "Hub and Agent versions are missing or different."
  exit 1
fi
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
if [[ ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
  echo "SOURCE_DATE_EPOCH must be a non-negative integer."
  exit 1
fi

DIST_DIR="$ROOT_DIR/dist"
STAGE_DIR="$(mktemp -d /tmp/dark-noc-release.XXXXXX)"
trap 'rm -rf "$STAGE_DIR"' EXIT
mkdir -p "$DIST_DIR" "$STAGE_DIR/dark-noc-pro" "$STAGE_DIR/dark-noc-node"

cp -a "$ROOT_DIR/hub" "$ROOT_DIR/agent" "$ROOT_DIR/deploy" "$ROOT_DIR/tests" "$STAGE_DIR/dark-noc-pro/"
cp "$ROOT_DIR/install-hub.sh" "$ROOT_DIR/upgrade.sh" "$ROOT_DIR/uninstall.sh" \
  "$ROOT_DIR/darknoc" \
  "$ROOT_DIR/README.md" "$ROOT_DIR/README.fa.md" "$ROOT_DIR/LICENSE" \
  "$ROOT_DIR/CHANGELOG.md" "$ROOT_DIR/SECURITY.md" "$ROOT_DIR/SUPPORT.md" "$STAGE_DIR/dark-noc-pro/"

mkdir -p "$STAGE_DIR/dark-noc-node/agent" "$STAGE_DIR/dark-noc-node/deploy"
cp -a "$ROOT_DIR/agent/." "$STAGE_DIR/dark-noc-node/agent/"
cp "$ROOT_DIR/deploy/dark-noc-agent.service" "$STAGE_DIR/dark-noc-node/deploy/"
cp "$ROOT_DIR/install-node.sh" "$ROOT_DIR/install-agent.sh" "$ROOT_DIR/upgrade.sh" "$ROOT_DIR/NODE-README.md" \
  "$ROOT_DIR/LICENSE" "$STAGE_DIR/dark-noc-node/"

build_archive() {
  local source_name="$1" destination="$2"
  tar --sort=name --format=gnu --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
    --exclude='__pycache__' --exclude='*.pyc' -cf - -C "$STAGE_DIR" "$source_name" | gzip -n > "$destination"
}

build_archive dark-noc-pro "$DIST_DIR/DARK-NOC-HUB-v$VERSION.tar.gz"
build_archive dark-noc-node "$DIST_DIR/DARK-NOC-NODE-v$VERSION.tar.gz"
(
  cd "$DIST_DIR"
  sha256sum "DARK-NOC-HUB-v$VERSION.tar.gz" "DARK-NOC-NODE-v$VERSION.tar.gz" > SHA256SUMS
)
echo "Release v$VERSION created in $DIST_DIR"
