#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

rm -rf ./.venv/lib/python3.11/site-packages/epub_generator
cp -r ../epub-generator/epub_generator ./.venv/lib/python3.11/site-packages/epub_generator
