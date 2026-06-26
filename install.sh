#!/bin/bash
# Thin shim: the real installer is install.py (cross-platform).
# macOS/Linux: ./install.sh    Windows: py install.py
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SRC/install.py" "$@"
