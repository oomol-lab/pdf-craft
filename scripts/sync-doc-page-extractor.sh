#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

rm -rf ./.venv/lib/python3.10/site-packages/doc_page_extractor
cp -r ../../moskize91/doc-page-extractor/doc_page_extractor ./.venv/lib/python3.10/site-packages/doc_page_extractor