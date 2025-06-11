#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

poetry run pylint "--generated-member=cv2" ./pdf_craft/**/*.py ./tests/**/*.py