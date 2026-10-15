#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
node "$ROOT_DIR/connector/tests/runtime.test.cjs"
node --test "$ROOT_DIR/connector/tests/carsi-preparation.test.cjs"

node --test "$ROOT_DIR/connector/tests/paper-enrichment.test.cjs"

node --test "$ROOT_DIR/connector/tests/journal-covers.test.cjs"
