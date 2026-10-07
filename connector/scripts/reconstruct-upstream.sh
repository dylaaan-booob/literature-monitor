#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONNECTOR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_FILE="$CONNECTOR_ROOT/upstream.lock"
DESTINATION="${1:-}"

fail() {
    printf 'reconstruct-upstream: %s\n' "$*" >&2
    exit 1
}

if [[ -z "$DESTINATION" ]]; then
    fail "usage: $0 <empty-destination-path>"
fi

if [[ -e "$DESTINATION" ]]; then
    fail "destination already exists: $DESTINATION"
fi

for command in git awk sort cmp mktemp; do
    command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

UPSTREAM_REPOSITORY="$(awk '$1 == "upstream" { print $2 }' "$LOCK_FILE")"
UPSTREAM_REVISION="$(awk '$1 == "upstream" { print $3 }' "$LOCK_FILE")"

[[ "$UPSTREAM_REPOSITORY" == "https://github.com/zotero/zotero-connectors.git" ]] \
    || fail "unexpected upstream repository in $LOCK_FILE"
[[ "$UPSTREAM_REVISION" =~ ^[0-9a-f]{40}$ ]] \
    || fail "upstream revision is not a full Git SHA"

mkdir -p "$(dirname "$DESTINATION")"
git init -q "$DESTINATION"
git -C "$DESTINATION" remote add origin "$UPSTREAM_REPOSITORY"
GIT_TERMINAL_PROMPT=0 git -C "$DESTINATION" \
    -c protocol.version=2 \
    -c http.version=HTTP/1.1 \
    fetch --depth=1 --filter=blob:none origin "$UPSTREAM_REVISION"
git -C "$DESTINATION" checkout -q --detach FETCH_HEAD

ACTUAL_REVISION="$(git -C "$DESTINATION" rev-parse HEAD)"
[[ "$ACTUAL_REVISION" == "$UPSTREAM_REVISION" ]] \
    || fail "upstream checkout mismatch: expected $UPSTREAM_REVISION, got $ACTUAL_REVISION"

EXPECTED_SUBMODULES="$(mktemp)"
ACTUAL_SUBMODULES="$(mktemp)"
cleanup() {
    rm -f "$EXPECTED_SUBMODULES" "$ACTUAL_SUBMODULES"
}
trap cleanup EXIT

awk '$1 == "submodule" { print $2 " " $3 }' "$LOCK_FILE" | LC_ALL=C sort > "$EXPECTED_SUBMODULES"
git -C "$DESTINATION" ls-files --stage \
    | awk '$1 == "160000" { print $4 " " $2 }' \
    | LC_ALL=C sort > "$ACTUAL_SUBMODULES"

cmp -s "$EXPECTED_SUBMODULES" "$ACTUAL_SUBMODULES" \
    || fail "upstream gitlink set does not match $LOCK_FILE"

GIT_TERMINAL_PROMPT=0 git -C "$DESTINATION" \
    -c http.version=HTTP/1.1 \
    submodule update --init --depth=1

while read -r kind path revision; do
    [[ "$kind" == "submodule" ]] || continue
    actual="$(git -C "$DESTINATION/$path" rev-parse HEAD)"
    [[ "$actual" == "$revision" ]] \
        || fail "submodule mismatch for $path: expected $revision, got $actual"
done < "$LOCK_FILE"

cmp -s "$CONNECTOR_ROOT/COPYING" "$DESTINATION/COPYING" \
    || fail "connector/COPYING does not match the pinned upstream COPYING"

printf 'Reconstructed zotero/zotero-connectors %s at %s\n' \
    "$UPSTREAM_REVISION" "$DESTINATION"
