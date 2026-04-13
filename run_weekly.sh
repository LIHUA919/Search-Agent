#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 collector.py --insecure
