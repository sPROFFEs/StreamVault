#!/usr/bin/env bash
# ==============================================================================
# StreamVault Top-Level Installer Wrapper
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/vault/setup.sh" ]]; then
    exec bash "$SCRIPT_DIR/vault/setup.sh" "$@"
else
    echo "Error: vault/setup.sh not found in $SCRIPT_DIR" >&2
    exit 1
fi
