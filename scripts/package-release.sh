#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: package-release.sh [options]

Build and package Refiner release artifacts.

Options:
  --version VERSION     Expected package version from pyproject.toml.
  --deb-version VER     Debian package version. Default: project version.
  --deb-channel-alias   Optional stable Debian channel alias filename prefix.
                        Example: latest-main -> refiner_latest-main_all.deb
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
DEB_VERSION=
DEB_CHANNEL_ALIAS=
OUTPUT_DIR=
PYTHON_BIN=python3

while (($#)); do
  case "$1" in
    --version)
      shift
      (($#)) || die "--version requires a value"
      VERSION="$1"
      ;;
    --deb-version)
      shift
      (($#)) || die "--deb-version requires a value"
      DEB_VERSION="$1"
      ;;
    --deb-channel-alias)
      shift
      (($#)) || die "--deb-channel-alias requires a value"
      DEB_CHANNEL_ALIAS="$1"
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
if [[ -z "$DEB_VERSION" ]]; then
  DEB_VERSION="$PROJECT_VERSION"
fi

OUTPUT_DIR=$(mkdir -p "$OUTPUT_DIR" && cd "$OUTPUT_DIR" && pwd)
find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

log "building python distribution artifacts"
"$PYTHON_BIN" -m build --outdir "$OUTPUT_DIR"
"$PYTHON_BIN" -m twine check "$OUTPUT_DIR"/*

log "building Debian package artifact"
command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb is required"

deb_stage_root="$OUTPUT_DIR/.deb-stage"
deb_root="$deb_stage_root/refiner"
deb_path="$OUTPUT_DIR/refiner_${DEB_VERSION}_all.deb"

rm -rf "$deb_stage_root"
mkdir -p "$deb_root/DEBIAN" "$deb_root/opt/refiner/src" "$deb_root/usr/local/bin"

cat >"$deb_root/DEBIAN/control" <<EOF
Package: refiner
Version: ${DEB_VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: NeuralMimicry
Depends: python3 (>= 3.11), python3-venv
Description: Refiner service source bundle and startup wrapper
EOF

cat >"$deb_root/usr/local/bin/refiner-start" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /opt/refiner/src/scripts/start_refiner_stack.sh "$@"
EOF
chmod 0755 "$deb_root/usr/local/bin/refiner-start"

tar \
  --exclude-vcs \
  --exclude='./.agent' \
  --exclude='./.github' \
  --exclude='./.idea' \
  --exclude='./.pytest_cache' \
  --exclude='./.research_cache' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./__pycache__' \
  --exclude='./job_data' \
  --exclude='./target' \
  --exclude='./dist' \
  -cf - . | tar -C "$deb_root/opt/refiner/src" -xf -

dpkg-deb --build --root-owner-group "$deb_root" "$deb_path" >/dev/null

if [[ -n "$DEB_CHANNEL_ALIAS" ]]; then
  cp "$deb_path" "$OUTPUT_DIR/refiner_${DEB_CHANNEL_ALIAS}_all.deb"
fi

rm -rf "$deb_stage_root"

CHECKSUM_PATH="$OUTPUT_DIR/refiner-${DEB_VERSION}.sha256.txt"
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
