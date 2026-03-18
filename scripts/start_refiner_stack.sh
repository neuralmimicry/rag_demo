#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./scripts/start_refiner_stack.sh [--once] [--no-build] [--help]

Options:
  --once      Start STT + refiner_web once; do not auto-restart on crash.
  --no-build  Skip Rust STT build step if binary is missing.
  --help      Show this help.

Key environment variables:
  STT_MODEL                Path to whisper model (.bin). If missing, auto-download can populate it.
  STT_MODEL_PROFILE        Auto-download profile name (default: tiny.en)
  STT_MODEL_URL            Override auto-download URL.
  STT_MODEL_SHA1           Optional SHA1 checksum override for downloaded model.
  STT_AUTO_DOWNLOAD        Auto-download model if not present (default: 1)
  STT_BIND                 STT bind address (default: 127.0.0.1:7079)
  STT_LANG                 STT language (default: en-GB)
  STT_THREADS              Threads per inference (default: 2)
  STT_WORKERS              STT worker count (default: cpu/2, min 1)
  STT_MAX_AUDIO_BYTES      Max upload bytes (default: 8000000)
  STT_BUILD                Build STT binary if missing (default: 1)

  REFINER_HOST             Refiner bind host (default: 127.0.0.1)
  REFINER_PORT             Refiner bind port (default: 5001)
  PYTHON_BIN               Python executable (default: .venv/bin/python, then python3)
  LOG_DIR                  Log directory (default: job_data/logs)

  HEALTH_TIMEOUT_SEC       Startup health timeout in seconds (default: 60)
  RESTART_BACKOFF_SEC      Restart delay in seconds (default: 3)
  MAX_RESTARTS             Max automatic restarts; 0 = infinite (default: 0)
EOF
}

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || die "Missing command: ${cmd}"
}

flag_enabled() {
  local raw="${1:-}"
  local normalized
  normalized="$(printf '%s' "${raw}" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "${normalized}" in
    1|true|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

cpu_count() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
    return
  fi
  getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2
}

default_stt_workers() {
  local cpus
  cpus="$(cpu_count)"
  local workers=$(( cpus / 2 ))
  if (( workers < 1 )); then
    workers=1
  fi
  echo "${workers}"
}

detect_default_model() {
  local candidate
  for candidate in \
    "/opt/refiner/models/ggml-base.en.bin" \
    "/opt/refiner/models/ggml-tiny.en.bin" \
    "${ROOT_DIR}/stt_rust/models/ggml-base.en.bin"; do
    if [[ -f "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done

  local discovered
  discovered="$(find "${ROOT_DIR}/stt_rust" -maxdepth 3 -type f -name 'ggml-*.bin' 2>/dev/null | head -n 1 || true)"
  if [[ -n "${discovered}" ]]; then
    echo "${discovered}"
    return 0
  fi
  return 1
}

default_model_target() {
  local profile="$1"
  local safe_profile
  safe_profile="$(printf '%s' "${profile}" | tr -cd 'A-Za-z0-9._-')"
  if [[ -z "${safe_profile}" ]]; then
    safe_profile="tiny.en"
  fi
  echo "${ROOT_DIR}/stt_rust/models/ggml-${safe_profile}.bin"
}

model_url_for_profile() {
  local profile="$1"
  case "${profile}" in
    tiny|tiny.en|tiny-q5_1|tiny.en-q5_1|base|base.en)
      echo "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-${profile}.bin"
      ;;
    *)
      echo ""
      ;;
  esac
}

model_sha1_for_profile() {
  local profile="$1"
  case "${profile}" in
    tiny) echo "bd577a113a864445d4c299885e0cb97d4ba92b5f" ;;
    tiny.en) echo "c78c86eb1a8faa21b369bcd33207cc90d64ae9df" ;;
    tiny-q5_1) echo "28276682c2d23fabe40cf6f9f41ffedb8283d445" ;;
    tiny.en-q5_1) echo "3fb922f6012d55ac7aa6e6c4b9221fa319db7461" ;;
    base) echo "465707469ff3a37a2b9b8d8f89f2f99de7299dac" ;;
    base.en) echo "137c40403d78fd54d454da0f9bd998f78703390c" ;;
    *) echo "" ;;
  esac
}

download_model_file() {
  local target="$1"
  local url="$2"
  local expected_sha1="$3"
  local tmp
  tmp="${target}.part.$$"
  mkdir -p "$(dirname "${target}")"

  log "Downloading STT model from ${url}"
  if ! curl --fail --location --retry 5 --retry-delay 2 --retry-all-errors --output "${tmp}" "${url}"; then
    rm -f "${tmp}" || true
    return 1
  fi

  if [[ -n "${expected_sha1}" && -x "$(command -v sha1sum || true)" ]]; then
    local actual_sha1
    actual_sha1="$(sha1sum "${tmp}" | awk '{print $1}')"
    if [[ "${actual_sha1}" != "${expected_sha1}" ]]; then
      rm -f "${tmp}" || true
      die "Downloaded model checksum mismatch. Expected ${expected_sha1}, got ${actual_sha1}"
    fi
  fi

  mv "${tmp}" "${target}"
  log "Model ready: ${target}"
}

wait_for_http() {
  local url="$1"
  local name="$2"
  local pid="$3"
  local timeout="$4"
  local started_at
  started_at="$(date +%s)"

  while true; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      log "${name} exited before becoming healthy."
      return 1
    fi
    if curl --silent --show-error --fail --max-time 2 "${url}" >/dev/null 2>&1; then
      log "${name} is healthy: ${url}"
      return 0
    fi
    local now
    now="$(date +%s)"
    if (( now - started_at >= timeout )); then
      log "${name} did not become healthy within ${timeout}s: ${url}"
      return 1
    fi
    sleep 1
  done
}

terminate_pid() {
  local pid="$1"
  local name="$2"
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  if ! kill -0 "${pid}" 2>/dev/null; then
    return 0
  fi
  log "Stopping ${name} (pid=${pid})"
  kill "${pid}" 2>/dev/null || true
  local i
  for i in {1..10}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  log "Force killing ${name} (pid=${pid})"
  kill -9 "${pid}" 2>/dev/null || true
}

STT_PID=""
REFINER_PID=""
SHUTTING_DOWN=0

stop_children() {
  terminate_pid "${REFINER_PID}" "refiner_web"
  terminate_pid "${STT_PID}" "stt"
  REFINER_PID=""
  STT_PID=""
}

on_signal() {
  SHUTTING_DOWN=1
  log "Received shutdown signal."
  stop_children
  exit 0
}

trap on_signal INT TERM
trap 'stop_children' EXIT

ONCE=0
while (($#)); do
  case "$1" in
    --once)
      ONCE=1
      shift
      ;;
    --no-build)
      export STT_BUILD=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

STT_DIR="${STT_DIR:-${ROOT_DIR}/stt_rust}"
STT_BIN="${STT_BIN:-${STT_DIR}/target/release/refiner-stt}"
STT_MODEL_PROFILE="${STT_MODEL_PROFILE:-tiny.en}"
STT_MODEL="${STT_MODEL:-}"
if [[ -z "${STT_MODEL}" ]]; then
  STT_MODEL="$(detect_default_model || true)"
fi
STT_MODEL_URL="${STT_MODEL_URL:-}"
STT_MODEL_SHA1="${STT_MODEL_SHA1:-}"
STT_AUTO_DOWNLOAD="${STT_AUTO_DOWNLOAD:-1}"

STT_BIND="${STT_BIND:-127.0.0.1:7079}"
STT_LANG="${STT_LANG:-en-GB}"
STT_THREADS="${STT_THREADS:-2}"
STT_WORKERS="${STT_WORKERS:-$(default_stt_workers)}"
STT_MAX_AUDIO_BYTES="${STT_MAX_AUDIO_BYTES:-8000000}"
STT_BUILD="${STT_BUILD:-1}"

REFINER_WEB_SCRIPT="${REFINER_WEB_SCRIPT:-${ROOT_DIR}/refiner_web.py}"
REFINER_HOST="${REFINER_HOST:-127.0.0.1}"
REFINER_PORT="${REFINER_PORT:-5001}"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

LOG_DIR="${LOG_DIR:-${ROOT_DIR}/job_data/logs}"
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-60}"
RESTART_BACKOFF_SEC="${RESTART_BACKOFF_SEC:-3}"
MAX_RESTARTS="${MAX_RESTARTS:-0}"

stt_host="${STT_BIND%%:*}"
stt_port="${STT_BIND##*:}"
if [[ "${stt_host}" == "0.0.0.0" || "${stt_host}" == "::" || "${stt_host}" == "[::]" ]]; then
  stt_host="127.0.0.1"
fi

STT_HEALTH_URL="${STT_HEALTH_URL:-http://${stt_host}:${stt_port}/health}"
REFINER_HEALTH_URL="${REFINER_HEALTH_URL:-http://${REFINER_HOST}:${REFINER_PORT}/api/health}"

require_cmd curl
require_cmd "${PYTHON_BIN}"

[[ -x "${PYTHON_BIN}" ]] || die "Python executable not found or not executable: ${PYTHON_BIN}"
[[ -f "${REFINER_WEB_SCRIPT}" ]] || die "Missing refiner web script: ${REFINER_WEB_SCRIPT}"

if [[ -z "${STT_MODEL}" ]]; then
  if flag_enabled "${STT_AUTO_DOWNLOAD}"; then
    STT_MODEL="$(default_model_target "${STT_MODEL_PROFILE}")"
  else
    die "No STT model found. Set STT_MODEL to a whisper .bin file."
  fi
fi

if [[ ! -f "${STT_MODEL}" ]]; then
  if flag_enabled "${STT_AUTO_DOWNLOAD}"; then
    if [[ -z "${STT_MODEL_URL}" ]]; then
      STT_MODEL_URL="$(model_url_for_profile "${STT_MODEL_PROFILE}")"
    fi
    if [[ -z "${STT_MODEL_SHA1}" ]]; then
      STT_MODEL_SHA1="$(model_sha1_for_profile "${STT_MODEL_PROFILE}")"
    fi
    [[ -n "${STT_MODEL_URL}" ]] || die "No download URL for profile '${STT_MODEL_PROFILE}'. Set STT_MODEL_URL."
    if ! download_model_file "${STT_MODEL}" "${STT_MODEL_URL}" "${STT_MODEL_SHA1}"; then
      die "Failed to download STT model. Set STT_MODEL manually or provide STT_MODEL_URL."
    fi
  else
    die "STT model file not found: ${STT_MODEL}"
  fi
fi

if [[ ! -x "${STT_BIN}" ]]; then
  if [[ "${STT_BUILD}" != "1" ]]; then
    die "STT binary missing and STT_BUILD=0: ${STT_BIN}"
  fi
  require_cmd cargo
  log "Building STT binary..."
  (
    cd "${STT_DIR}"
    RUSTFLAGS="${RUSTFLAGS:--C target-cpu=native}" cargo build --release
  )
fi

mkdir -p "${LOG_DIR}"
STT_LOG="${LOG_DIR}/stt.log"
REFINER_LOG="${LOG_DIR}/refiner_web.log"

export REFINER_HOST
export REFINER_PORT
export REFINER_STT_BACKEND="server"
export REFINER_STT_SERVER_URL="${REFINER_STT_SERVER_URL:-http://${stt_host}:${stt_port}}"
export REFINER_STT_SERVER_TIMEOUT="${REFINER_STT_SERVER_TIMEOUT:-25}"
export REFINER_STT_SERVER_PREPROCESS="${REFINER_STT_SERVER_PREPROCESS:-0}"
export REFINER_STT_SERVER_SEND_PROMPT="${REFINER_STT_SERVER_SEND_PROMPT:-0}"

if [[ -z "${REFINER_SECRET_KEY:-}" ]]; then
  log "Warning: REFINER_SECRET_KEY is not set; Flask sessions will be transient."
fi

start_stt() {
  log "Starting STT service..."
  "${STT_BIN}" \
    --model "${STT_MODEL}" \
    --bind "${STT_BIND}" \
    --lang "${STT_LANG}" \
    --threads "${STT_THREADS}" \
    --workers "${STT_WORKERS}" \
    --max-audio-bytes "${STT_MAX_AUDIO_BYTES}" \
    >>"${STT_LOG}" 2>&1 &
  STT_PID="$!"

  wait_for_http "${STT_HEALTH_URL}" "STT" "${STT_PID}" "${HEALTH_TIMEOUT_SEC}"
}

start_refiner() {
  log "Starting refiner_web..."
  "${PYTHON_BIN}" "${REFINER_WEB_SCRIPT}" >>"${REFINER_LOG}" 2>&1 &
  REFINER_PID="$!"

  wait_for_http "${REFINER_HEALTH_URL}" "refiner_web" "${REFINER_PID}" "${HEALTH_TIMEOUT_SEC}"
}

restart_count=0
while true; do
  if ! start_stt; then
    stop_children
    if (( ONCE == 1 )); then
      die "Failed to start STT."
    fi
  elif ! start_refiner; then
    stop_children
    if (( ONCE == 1 )); then
      die "Failed to start refiner_web."
    fi
  else
    log "Stack is healthy. Logs:"
    log "  STT: ${STT_LOG}"
    log "  Refiner: ${REFINER_LOG}"
    wait -n "${STT_PID}" "${REFINER_PID}" || true
    if (( SHUTTING_DOWN == 1 )); then
      exit 0
    fi
    if ! kill -0 "${STT_PID}" 2>/dev/null; then
      log "STT exited unexpectedly."
    fi
    if ! kill -0 "${REFINER_PID}" 2>/dev/null; then
      log "refiner_web exited unexpectedly."
    fi
    stop_children
    if (( ONCE == 1 )); then
      die "A managed process exited."
    fi
  fi

  restart_count=$((restart_count + 1))
  if (( MAX_RESTARTS > 0 && restart_count > MAX_RESTARTS )); then
    die "Exceeded MAX_RESTARTS=${MAX_RESTARTS}."
  fi
  log "Restarting stack in ${RESTART_BACKOFF_SEC}s (attempt ${restart_count})..."
  sleep "${RESTART_BACKOFF_SEC}"
done
