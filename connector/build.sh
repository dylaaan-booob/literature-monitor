#!/usr/bin/env bash
set -euo pipefail

CONNECTOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$CONNECTOR_ROOT/.." && pwd -P)"
CONNECTOR_VERSION="0.6.1"
DEFAULT_WORK_ROOT="$CONNECTOR_ROOT/.work"
DEFAULT_OUTPUT_DIR="$CONNECTOR_ROOT/build/chrome-mv3"
WORK_ROOT_IS_EXTERNAL=0
OUTPUT_DIR_IS_EXTERNAL=0

if [[ ${LM_CONNECTOR_WORK_DIR+x} ]]; then
    WORK_ROOT_IS_EXTERNAL=1
    WORK_ROOT="$LM_CONNECTOR_WORK_DIR"
else
    WORK_ROOT="$DEFAULT_WORK_ROOT"
fi

if [[ ${LM_CONNECTOR_OUTPUT_DIR+x} ]]; then
    OUTPUT_DIR_IS_EXTERNAL=1
    OUTPUT_DIR="$LM_CONNECTOR_OUTPUT_DIR"
else
    OUTPUT_DIR="$DEFAULT_OUTPUT_DIR"
fi

fail() {
    printf 'connector-build: %s\n' "$*" >&2
    exit 1
}

canonicalize_external_path() {
    local raw="$1"
    local label="$2"
    local allow_existing_directory="$3"
    local parent
    local leaf
    local physical_parent

    [[ -n "$raw" ]] || fail "$label must not be empty"
    [[ ! -L "$raw" ]] || fail "$label must not be a symlink: $raw"

    if [[ -e "$raw" ]]; then
        if [[ "$allow_existing_directory" != "yes" ]]; then
            fail "$label must not already exist: $raw"
        fi
        [[ -d "$raw" ]] || fail "$label must be a directory: $raw"
        (cd "$raw" && pwd -P)
        return
    fi

    parent="$(dirname "$raw")"
    leaf="$(basename "$raw")"
    if [[ "$leaf" == "." || "$leaf" == ".." ]]; then
        fail "$label has an unsafe terminal component: $raw"
    fi
    [[ -d "$parent" ]] || fail "$label parent directory must already exist: $parent"
    physical_parent="$(cd "$parent" && pwd -P)"
    printf '%s/%s\n' "$physical_parent" "$leaf"
}

path_is_same_or_ancestor() {
    local candidate="$1"
    local protected="$2"

    [[ "$candidate" == "/" ]] && return 0
    [[ "$candidate" == "$protected" || "$protected" == "$candidate/"* ]]
}

path_is_inside_git_worktree() {
    local cursor="$1"

    [[ -d "$cursor" ]] || cursor="$(dirname "$cursor")"
    while [[ "$cursor" != "/" ]]; do
        [[ ! -e "$cursor/.git" && ! -L "$cursor/.git" ]] || return 0
        cursor="$(dirname "$cursor")"
    done
    return 1
}

reject_dangerous_external_root() {
    local path="$1"
    local label="$2"
    local home_root

    home_root="$(cd "$HOME" && pwd -P)"
    if path_is_same_or_ancestor "$path" "$REPOSITORY_ROOT"; then
        fail "$label cannot be the repository or one of its ancestors: $path"
    fi
    if path_is_same_or_ancestor "$path" "$home_root"; then
        fail "$label cannot be the user home directory or one of its ancestors: $path"
    fi
    if path_is_inside_git_worktree "$path"; then
        fail "$label cannot be inside a Git worktree: $path"
    fi
}

if [[ "$WORK_ROOT_IS_EXTERNAL" -eq 1 ]]; then
    WORK_ROOT="$(canonicalize_external_path "$WORK_ROOT" "external work directory" yes)"
    reject_dangerous_external_root "$WORK_ROOT" "external work directory"
else
    [[ ! -L "$WORK_ROOT" ]] || fail "managed work directory must not be a symlink: $WORK_ROOT"
    [[ ! -e "$WORK_ROOT" || -d "$WORK_ROOT" ]] || fail "managed work directory is not a directory: $WORK_ROOT"
fi

SOURCE_DIR="$WORK_ROOT/upstream"
UPSTREAM_BUILD_DIR="$SOURCE_DIR/build"

if [[ "$WORK_ROOT_IS_EXTERNAL" -eq 1 ]]; then
    [[ ! -e "$SOURCE_DIR" && ! -L "$SOURCE_DIR" ]] || fail "external upstream source target must not already exist: $SOURCE_DIR"
else
    [[ ! -L "$SOURCE_DIR" ]] || fail "managed upstream source target must not be a symlink: $SOURCE_DIR"
    [[ ! -e "$SOURCE_DIR" || -d "$SOURCE_DIR" ]] || fail "managed upstream source target is not a directory: $SOURCE_DIR"
fi

if [[ "$OUTPUT_DIR_IS_EXTERNAL" -eq 1 ]]; then
    OUTPUT_DIR="$(canonicalize_external_path "$OUTPUT_DIR" "external output directory" no)"
    reject_dangerous_external_root "$OUTPUT_DIR" "external output directory"
else
    [[ ! -L "$CONNECTOR_ROOT/build" ]] || fail "managed output parent must not be a symlink: $CONNECTOR_ROOT/build"
    [[ ! -e "$CONNECTOR_ROOT/build" || -d "$CONNECTOR_ROOT/build" ]] || fail "managed output parent is not a directory: $CONNECTOR_ROOT/build"
    [[ ! -L "$OUTPUT_DIR" ]] || fail "managed output directory must not be a symlink: $OUTPUT_DIR"
    [[ ! -e "$OUTPUT_DIR" || -d "$OUTPUT_DIR" ]] || fail "managed output path is not a directory: $OUTPUT_DIR"
fi

if [[ "$SOURCE_DIR" == "$OUTPUT_DIR" || "$SOURCE_DIR" == "$OUTPUT_DIR/"* || "$OUTPUT_DIR" == "$SOURCE_DIR/"* ]]; then
    fail "upstream source and output directories must be disjoint"
fi

for command in git node npm rsync jq perl awk sort cmp mktemp grep find; do
    command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

if [[ "$WORK_ROOT_IS_EXTERNAL" -eq 0 ]]; then
    rm -rf "$SOURCE_DIR"
    mkdir -p "$WORK_ROOT"
elif [[ ! -e "$WORK_ROOT" ]]; then
    mkdir "$WORK_ROOT"
fi
"$CONNECTOR_ROOT/scripts/reconstruct-upstream.sh" "$SOURCE_DIR"

PATCH_LIST="$(mktemp)"
EXPECTED_PATCH_PATHS="$(mktemp)"
ACTUAL_PATCH_PATHS="$(mktemp)"
cleanup() {
    rm -f "$PATCH_LIST" "$EXPECTED_PATCH_PATHS" "$ACTUAL_PATCH_PATHS"
}
trap cleanup EXIT

find "$CONNECTOR_ROOT/patches" -maxdepth 1 -type f -name '*.patch' -print \
    | LC_ALL=C sort > "$PATCH_LIST"

while IFS= read -r patch; do
    [[ -n "$patch" ]] || continue
    git -C "$SOURCE_DIR" apply --check "$patch"
    git -C "$SOURCE_DIR" apply "$patch"
done < "$PATCH_LIST"

awk '$1 == "patch" { print $2 }' "$CONNECTOR_ROOT/delta.lock" \
    | LC_ALL=C sort > "$EXPECTED_PATCH_PATHS"
git -C "$SOURCE_DIR" diff --name-only | LC_ALL=C sort > "$ACTUAL_PATCH_PATHS"
cmp -s "$EXPECTED_PATCH_PATHS" "$ACTUAL_PATCH_PATHS" \
    || fail "applied upstream source delta does not match delta.lock"

(
    cd "$SOURCE_DIR"
    npm ci --ignore-scripts --no-audit --no-fund
    ./build.sh -d -v "$CONNECTOR_VERSION"
)

node - "$UPSTREAM_BUILD_DIR/manifestv3/manifest.json" "$CONNECTOR_VERSION" <<'NODE'
const fs = require('fs');
const path = process.argv[2];
const expectedVersion = process.argv[3];
const manifest = JSON.parse(fs.readFileSync(path, 'utf8'));
if (manifest.manifest_version !== 3) {
    throw new Error(`expected manifest_version 3, got ${manifest.manifest_version}`);
}
if (manifest.name !== 'Literature Monitor Connector') {
    throw new Error(`unexpected Connector name: ${manifest.name}`);
}
if (manifest.version !== expectedVersion) {
    throw new Error(`expected Connector version ${expectedVersion}, got ${manifest.version}`);
}
NODE

test -f "$UPSTREAM_BUILD_DIR/manifestv3/COPYING" \
    || fail "upstream build artifact is missing COPYING"

if [[ "$OUTPUT_DIR_IS_EXTERNAL" -eq 0 ]]; then
    rm -rf "$OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR"
else
    mkdir "$OUTPUT_DIR"
fi
rsync -a "$UPSTREAM_BUILD_DIR/manifestv3/" "$OUTPUT_DIR/"

while read -r kind path; do
    [[ "$kind" == "overlay" ]] || continue
    source="$CONNECTOR_ROOT/overlay/$path"
    [[ -f "$source" ]] || fail "missing tracked overlay: $source"
    cp "$source" "$OUTPUT_DIR/$path"
done < "$CONNECTOR_ROOT/delta.lock"

grep -Fq "$(awk '$1 == "upstream" { print $3 }' "$CONNECTOR_ROOT/upstream.lock")" \
    "$OUTPUT_DIR/LITERATURE_MONITOR_PROVENANCE.txt" \
    || fail "artifact provenance marker does not match upstream.lock"

test -f "$OUTPUT_DIR/literature-monitor-runtime.js" \
    || fail "artifact is missing the Literature Monitor runtime overlay"
grep -Fq '"literature-monitor-runtime.js"' "$OUTPUT_DIR/background-worker.js" \
    || fail "MV3 service worker is not wired to the Literature Monitor runtime"
grep -Fq 'const BRIDGE_BASE = "http://127.0.0.1:8000";' \
    "$OUTPUT_DIR/literature-monitor-runtime.js" \
    || fail "Literature Monitor runtime bridge authority is missing or changed"
BRIDGE_URLS="$(
    grep -Eo 'https?://[^"[:space:]]+' "$OUTPUT_DIR/literature-monitor-runtime.js" \
        | LC_ALL=C sort -u
)"
[[ "$BRIDGE_URLS" == "http://127.0.0.1:8000" ]] \
    || fail "Literature Monitor runtime contains an unexpected network authority"
cmp -s "$CONNECTOR_ROOT/COPYING" "$OUTPUT_DIR/COPYING" \
    || fail "artifact COPYING differs from the pinned upstream license copy"

printf 'Built Literature Monitor Connector Chrome/MV3 artifact: %s\n' "$OUTPUT_DIR"
