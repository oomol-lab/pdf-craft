#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

rm -rf ./.venv/lib/python3.10/site-packages/epub_generator
cp -r ../../moskize91/epub-generator/epub_generator ./.venv/lib/python3.10/site-packages/epub_generator