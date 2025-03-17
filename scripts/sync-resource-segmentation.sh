#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

rm -rf ./.venv/lib/python3.10/site-packages/resource-segmentation
cp -r ../../moskize91/resource-segmentation/resource_segmentation ./.venv/lib/python3.10/site-packages/resource-segmentation