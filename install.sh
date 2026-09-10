#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="darktunnelmika/dark-noc"
COMPONENT="${1:-}"

if [[ "$COMPONENT" != "hub" && "$COMPONENT" != "node" ]]; then
  echo "DARK NOC Quick Installer · @mikakhadm"
  echo "Usage: bash <(curl -fsSL https://raw.githubusercontent.com/$REPOSITORY/main/install.sh) hub|node"
  exit 1
fi
if [[ ${EUID:-999} -ne 0 ]]; then
  echo "Run as root: sudo -i"
  exit 1
fi
for command in curl tar sha256sum mktemp; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing required command: $command"; exit 1; }
done

LATEST_URL="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPOSITORY/releases/latest")"
VERSION="${LATEST_URL##*/}"
if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Could not resolve the latest stable DARK NOC release."
  exit 1
fi

if [[ "$COMPONENT" == "hub" ]]; then
  ASSET="DARK-NOC-HUB-$VERSION.tar.gz"
  PROJECT_DIR="dark-noc-pro"
  INSTALLER="install-hub.sh"
else
  ASSET="DARK-NOC-NODE-$VERSION.tar.gz"
  PROJECT_DIR="dark-noc-node"
  INSTALLER="install-node.sh"
fi

BASE_URL="https://github.com/$REPOSITORY/releases/download/$VERSION"
WORK_DIR="$(mktemp -d /tmp/dark-noc-install.XXXXXX)"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

echo "DARK NOC $VERSION · downloading $COMPONENT package"
curl --proto '=https' --tlsv1.2 -fL --retry 3 -o "$WORK_DIR/$ASSET" "$BASE_URL/$ASSET"
curl --proto '=https' --tlsv1.2 -fL --retry 3 -o "$WORK_DIR/SHA256SUMS" "$BASE_URL/SHA256SUMS"

EXPECTED="$(awk -v asset="$ASSET" '$2 == asset {print $1}' "$WORK_DIR/SHA256SUMS")"
ACTUAL="$(sha256sum "$WORK_DIR/$ASSET" | awk '{print $1}')"
if [[ -z "$EXPECTED" || "$EXPECTED" != "$ACTUAL" ]]; then
  echo "SHA-256 verification failed; installation stopped."
  exit 1
fi

tar -xzf "$WORK_DIR/$ASSET" -C "$WORK_DIR"
if [[ ! -f "$WORK_DIR/$PROJECT_DIR/$INSTALLER" ]]; then
  echo "Installer was not found inside the verified package."
  exit 1
fi
chmod +x "$WORK_DIR/$PROJECT_DIR"/*.sh
echo "Package verified. Starting DARK NOC $COMPONENT installer..."
(cd "$WORK_DIR/$PROJECT_DIR" && bash "$INSTALLER")

