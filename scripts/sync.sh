#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

rm -rf ./doc_page_extractor
cp -r ../../moskize91/doc_page_extractor/doc-page-extractor ./doc_page_extractor