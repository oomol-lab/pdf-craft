#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

rm -rf ./.venv/lib/python3.11/site-packages/doc_page_extractor
cp -r ../doc-page-extractor/doc_page_extractor ./.venv/lib/python3.11/site-packages/doc_page_extractor
