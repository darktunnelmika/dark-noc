#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
echo "install-agent.sh is now a compatibility alias for zero-touch Node prerequisites."
exec bash "$SCRIPT_DIR/install-node.sh" "$@"
