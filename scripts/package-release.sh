#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: package-release.sh [options]

Build and package Refiner release artifacts.

Options:
  --version VERSION     Expected package version from pyproject.toml.
  --output-dir DIR      Directory to receive packaged artifacts.
  --python BIN          Python interpreter to use. Default: python3.
  -h, --help            Show this help text.

Examples:
  ./scripts/package-release.sh --version 0.1.0 --output-dir ./dist
USAGE
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

sha256_tool() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf 'sha256sum\n'
  elif command -v shasum >/dev/null 2>&1; then
    printf 'shasum -a 256\n'
  else
    die "sha256sum or shasum is required"
  fi
}

read_project_version() {
  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
inside_project = False
for raw in Path('pyproject.toml').read_text(encoding='utf-8').splitlines():
    line = raw.strip()
    if line.startswith('[') and line.endswith(']'):
        inside_project = line == '[project]'
        continue
    if inside_project and line.startswith('version = '):
        print(line.split('=', 1)[1].strip().strip('"'))
        break
else:
    raise SystemExit('Unable to determine pyproject project.version')
PY
}

VERSION=
OUTPUT_DIR=
PYTHON_BIN=python3

while (($#)); do
  case "$1" in
    --version)
      shift
      (($#)) || die "--version requires a value"
      VERSION="$1"
      ;;
    --output-dir)
      shift
      (($#)) || die "--output-dir requires a value"
      OUTPUT_DIR="$1"
      ;;
    --python)
      shift
      (($#)) || die "--python requires a value"
      PYTHON_BIN="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
  shift
done

[[ -n "$OUTPUT_DIR" ]] || die "--output-dir is required"

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "python interpreter not found: $PYTHON_BIN"
PROJECT_VERSION=$(read_project_version)
if [[ -n "$VERSION" && "$VERSION" != "$PROJECT_VERSION" ]]; then
  die "pyproject.toml version ${PROJECT_VERSION} does not match requested version ${VERSION}"
fi

OUTPUT_DIR=$(mkdir -p "$OUTPUT_DIR" && cd "$OUTPUT_DIR" && pwd)
find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

log "building python distribution artifacts"
"$PYTHON_BIN" -m build --outdir "$OUTPUT_DIR"
"$PYTHON_BIN" -m twine check "$OUTPUT_DIR"/*

CHECKSUM_PATH="$OUTPUT_DIR/refiner-${PROJECT_VERSION}.sha256.txt"
checksum_cmd=$(sha256_tool)
(
  cd "$OUTPUT_DIR"
  artifacts=()
  for artifact in *; do
    [[ -f "$artifact" ]] || continue
    artifacts+=("$artifact")
  done
  [[ ${#artifacts[@]} -gt 0 ]] || die "no packaged artifacts were produced"
  $checksum_cmd "${artifacts[@]}" >"$(basename "$CHECKSUM_PATH")"
)

log
log "packaged Refiner release artifacts:"
find "$OUTPUT_DIR" -maxdepth 1 -type f | sort | sed 's#^#  #' 
