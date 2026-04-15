#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PARENT_DIR="$(cd "${ROOT_DIR}/.." && pwd)"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/start_refiner_stack.sh [--once] [--no-build] [--start-nmstt] [--help]

Options:
  --once         Start the processes once; do not auto-restart on crash.
  --no-build     Skip the nmstt build step if the binary is missing.
  --start-nmstt  Force management of a local nmstt process from ../nmstt.
  --help         Show this help.

Behavior:
  - If REFINER_STT_SERVER_URL is already configured, Refiner treats nmstt as an
    external dependency and does not start any local speech service.
  - If REFINER_STT_SERVER_URL is unset, this helper starts nmstt from the
    sibling ../nmstt repository by default so local development still works.

Preferred nmstt environment variables:
  NMSTT_DIR                 Path to the standalone nmstt repository.
  NMSTT_BIN                 Path to the nmstt binary.
  NMSTT_MODEL               Path to the Whisper model (.bin).
  NMSTT_MODEL_PROFILE       Auto-download profile name (default: tiny.en).
  NMSTT_MODEL_URL           Override auto-download URL.
  NMSTT_MODEL_SHA1          Optional SHA1 checksum override.
  NMSTT_AUTO_DOWNLOAD       Auto-download the model if it is missing (default: 1).
  NMSTT_BIND                Bind address for local nmstt (default: 127.0.0.1:7079).
  NMSTT_LANG                Language (default: en-GB).
  NMSTT_THREADS             Threads per inference (default: 2).
  NMSTT_WORKERS             Worker count (default: cpu/2, min 1).
  NMSTT_MAX_AUDIO_BYTES     Max upload bytes (default: 8000000).
  NMSTT_BUILD               Build the binary if missing (default: 1).

Legacy STT_* aliases are still accepted for local development compatibility.

Refiner environment variables:
  REFINER_STT_SERVER_URL    External nmstt base URL. If set, local nmstt is skipped.
  REFINER_HOST              Refiner bind host (default: 127.0.0.1).
  REFINER_PORT              Refiner bind port (default: 5001).
  PYTHON_BIN                Python executable (default: .venv/bin/python, then python3).
  LOG_DIR                   Log directory (default: job_data/logs).
  HEALTH_TIMEOUT_SEC        Startup health timeout in seconds (default: 60).
  RESTART_BACKOFF_SEC       Restart delay in seconds (default: 3).
  MAX_RESTARTS              Max automatic restarts; 0 = infinite (default: 0).
USAGE
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

default_nmstt_workers() {
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
    "/opt/nmstt/models/ggml-base.en.bin" \
    "/opt/nmstt/models/ggml-tiny.en.bin" \
    "${PARENT_DIR}/nmstt/models/ggml-base.en.bin" \
    "${PARENT_DIR}/nmstt/models/ggml-tiny.en.bin"; do
    if [[ -f "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done

  local discovered
  discovered="$(find "${PARENT_DIR}/nmstt" -maxdepth 3 -type f -name 'ggml-*.bin' 2>/dev/null | head -n 1 || true)"
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
  echo "${PARENT_DIR}/nmstt/models/ggml-${safe_profile}.bin"
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

  log "Downloading nmstt model from ${url}"
  if ! curl --fail --location --retry 5 --retry-delay 2 --retry-all-errors --output "${tmp}" "${url}"; then
    rm -f "${tmp}" || true
    return 1
  fi

  if [[ -n "${expected_sha1}" ]] && command -v sha1sum >/dev/null 2>&1; then
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

wait_for_http_with_pid() {
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

wait_for_http_endpoint() {
  local url="$1"
  local name="$2"
  local timeout="$3"
  local started_at
  started_at="$(date +%s)"

  while true; do
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

NMSTT_PID=""
REFINER_PID=""
MANAGE_LOCAL_NMSTT=0
SHUTTING_DOWN=0
FORCE_START_NMSTT=0

stop_children() {
  terminate_pid "${REFINER_PID}" "refiner_web"
  if (( MANAGE_LOCAL_NMSTT == 1 )); then
    terminate_pid "${NMSTT_PID}" "nmstt"
  fi
  REFINER_PID=""
  NMSTT_PID=""
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
      export NMSTT_BUILD=0
      export STT_BUILD=0
      shift
      ;;
    --start-nmstt)
      FORCE_START_NMSTT=1
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

NMSTT_DIR="${NMSTT_DIR:-${STT_DIR:-${PARENT_DIR}/nmstt}}"
NMSTT_BIN="${NMSTT_BIN:-${STT_BIN:-${NMSTT_DIR}/target/release/nmstt}}"
NMSTT_MODEL_PROFILE="${NMSTT_MODEL_PROFILE:-${STT_MODEL_PROFILE:-tiny.en}}"
NMSTT_MODEL="${NMSTT_MODEL:-${STT_MODEL:-}}"
if [[ -z "${NMSTT_MODEL}" ]]; then
  NMSTT_MODEL="$(detect_default_model || true)"
fi
NMSTT_MODEL_URL="${NMSTT_MODEL_URL:-${STT_MODEL_URL:-}}"
NMSTT_MODEL_SHA1="${NMSTT_MODEL_SHA1:-${STT_MODEL_SHA1:-}}"
NMSTT_AUTO_DOWNLOAD="${NMSTT_AUTO_DOWNLOAD:-${STT_AUTO_DOWNLOAD:-1}}"
NMSTT_BIND="${NMSTT_BIND:-${STT_BIND:-127.0.0.1:7079}}"
NMSTT_LANG="${NMSTT_LANG:-${STT_LANG:-en-GB}}"
NMSTT_THREADS="${NMSTT_THREADS:-${STT_THREADS:-2}}"
NMSTT_WORKERS="${NMSTT_WORKERS:-${STT_WORKERS:-$(default_nmstt_workers)}}"
NMSTT_MAX_AUDIO_BYTES="${NMSTT_MAX_AUDIO_BYTES:-${STT_MAX_AUDIO_BYTES:-8000000}}"
NMSTT_BUILD="${NMSTT_BUILD:-${STT_BUILD:-1}}"

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

nmstt_host="${NMSTT_BIND%%:*}"
nmstt_port="${NMSTT_BIND##*:}"
if [[ "${nmstt_host}" == "0.0.0.0" || "${nmstt_host}" == "::" || "${nmstt_host}" == "[::]" ]]; then
  nmstt_host="127.0.0.1"
fi

refiner_health_host="${REFINER_HOST}"
if [[ "${refiner_health_host}" == "0.0.0.0" || "${refiner_health_host}" == "::" || "${refiner_health_host}" == "[::]" ]]; then
  refiner_health_host="127.0.0.1"
fi

REFINER_HEALTH_URL="${REFINER_HEALTH_URL:-http://${refiner_health_host}:${REFINER_PORT}/api/health}"

require_cmd curl
require_cmd "${PYTHON_BIN}"

[[ -x "${PYTHON_BIN}" ]] || die "Python executable not found or not executable: ${PYTHON_BIN}"
[[ -f "${REFINER_WEB_SCRIPT}" ]] || die "Missing refiner web script: ${REFINER_WEB_SCRIPT}"

if (( FORCE_START_NMSTT == 1 )); then
  MANAGE_LOCAL_NMSTT=1
elif [[ -n "${REFINER_STT_SERVER_URL:-}" ]]; then
  MANAGE_LOCAL_NMSTT=0
else
  MANAGE_LOCAL_NMSTT=1
fi

if (( MANAGE_LOCAL_NMSTT == 1 )); then
  if [[ -z "${REFINER_STT_SERVER_URL:-}" ]]; then
    export REFINER_STT_SERVER_URL="http://${nmstt_host}:${nmstt_port}"
  fi
  NMSTT_HEALTH_URL="${NMSTT_HEALTH_URL:-${REFINER_STT_SERVER_URL%/}/health}"

  if [[ -z "${NMSTT_MODEL}" ]]; then
    if flag_enabled "${NMSTT_AUTO_DOWNLOAD}"; then
      NMSTT_MODEL="$(default_model_target "${NMSTT_MODEL_PROFILE}")"
    else
      die "No nmstt model found. Set NMSTT_MODEL to a whisper .bin file."
    fi
  fi

  if [[ ! -f "${NMSTT_MODEL}" ]]; then
    if flag_enabled "${NMSTT_AUTO_DOWNLOAD}"; then
      if [[ -z "${NMSTT_MODEL_URL}" ]]; then
        NMSTT_MODEL_URL="$(model_url_for_profile "${NMSTT_MODEL_PROFILE}")"
      fi
      if [[ -z "${NMSTT_MODEL_SHA1}" ]]; then
        NMSTT_MODEL_SHA1="$(model_sha1_for_profile "${NMSTT_MODEL_PROFILE}")"
      fi
      [[ -n "${NMSTT_MODEL_URL}" ]] || die "No download URL for profile '${NMSTT_MODEL_PROFILE}'. Set NMSTT_MODEL_URL."
      if ! download_model_file "${NMSTT_MODEL}" "${NMSTT_MODEL_URL}" "${NMSTT_MODEL_SHA1}"; then
        die "Failed to download nmstt model. Set NMSTT_MODEL manually or provide NMSTT_MODEL_URL."
      fi
    else
      die "nmstt model file not found: ${NMSTT_MODEL}"
    fi
  fi

  if [[ ! -x "${NMSTT_BIN}" ]]; then
    if [[ "${NMSTT_BUILD}" != "1" ]]; then
      die "nmstt binary missing and NMSTT_BUILD=0: ${NMSTT_BIN}"
    fi
    require_cmd cargo
    [[ -d "${NMSTT_DIR}" ]] || die "nmstt repository not found at ${NMSTT_DIR}"
    log "Building nmstt binary from ${NMSTT_DIR}"
    (
      cd "${NMSTT_DIR}"
      RUSTFLAGS="${RUSTFLAGS:--C target-cpu=native}" cargo build --release
    )
  fi
else
  [[ -n "${REFINER_STT_SERVER_URL:-}" ]] || die "REFINER_STT_SERVER_URL must be set when nmstt is externally managed."
  NMSTT_HEALTH_URL="${NMSTT_HEALTH_URL:-${REFINER_STT_SERVER_URL%/}/health}"
  log "Using externally managed nmstt at ${REFINER_STT_SERVER_URL}"
fi

mkdir -p "${LOG_DIR}"
NMSTT_LOG="${LOG_DIR}/nmstt.log"
REFINER_LOG="${LOG_DIR}/refiner_web.log"

export REFINER_HOST
export REFINER_PORT
export REFINER_STT_BACKEND="server"
export REFINER_STT_SERVER_TIMEOUT="${REFINER_STT_SERVER_TIMEOUT:-25}"
export REFINER_STT_SERVER_PREPROCESS="${REFINER_STT_SERVER_PREPROCESS:-0}"
export REFINER_STT_SERVER_SEND_PROMPT="${REFINER_STT_SERVER_SEND_PROMPT:-0}"

if [[ -z "${REFINER_SECRET_KEY:-}" ]]; then
  log "Warning: REFINER_SECRET_KEY is not set; Flask sessions will be transient."
fi

start_nmstt() {
  log "Starting nmstt service from ${NMSTT_DIR}"
  "${NMSTT_BIN}" \
    --model "${NMSTT_MODEL}" \
    --bind "${NMSTT_BIND}" \
    --lang "${NMSTT_LANG}" \
    --threads "${NMSTT_THREADS}" \
    --workers "${NMSTT_WORKERS}" \
    --max-audio-bytes "${NMSTT_MAX_AUDIO_BYTES}" \
    >>"${NMSTT_LOG}" 2>&1 &
  NMSTT_PID="$!"

  wait_for_http_with_pid "${NMSTT_HEALTH_URL}" "nmstt" "${NMSTT_PID}" "${HEALTH_TIMEOUT_SEC}"
}

wait_for_external_nmstt() {
  log "Waiting for externally managed nmstt at ${NMSTT_HEALTH_URL}"
  wait_for_http_endpoint "${NMSTT_HEALTH_URL}" "nmstt" "${HEALTH_TIMEOUT_SEC}"
}

start_refiner() {
  log "Starting refiner_web with REFINER_STT_SERVER_URL=${REFINER_STT_SERVER_URL}"
  "${PYTHON_BIN}" "${REFINER_WEB_SCRIPT}" >>"${REFINER_LOG}" 2>&1 &
  REFINER_PID="$!"

  wait_for_http_with_pid "${REFINER_HEALTH_URL}" "refiner_web" "${REFINER_PID}" "${HEALTH_TIMEOUT_SEC}"
}

restart_count=0
while true; do
  if (( MANAGE_LOCAL_NMSTT == 1 )); then
    if ! start_nmstt; then
      stop_children
      if (( ONCE == 1 )); then
        die "Failed to start nmstt."
      fi
    elif ! start_refiner; then
      stop_children
      if (( ONCE == 1 )); then
        die "Failed to start refiner_web."
      fi
    else
      log "Stack is healthy. Logs:"
      log "  nmstt: ${NMSTT_LOG}"
      log "  Refiner: ${REFINER_LOG}"
      wait -n "${NMSTT_PID}" "${REFINER_PID}" || true
      if (( SHUTTING_DOWN == 1 )); then
        exit 0
      fi
      if ! kill -0 "${NMSTT_PID}" 2>/dev/null; then
        log "nmstt exited unexpectedly."
      fi
      if ! kill -0 "${REFINER_PID}" 2>/dev/null; then
        log "refiner_web exited unexpectedly."
      fi
      stop_children
      if (( ONCE == 1 )); then
        die "A managed process exited."
      fi
    fi
  else
    if ! wait_for_external_nmstt; then
      if (( ONCE == 1 )); then
        die "External nmstt is unavailable."
      fi
    elif ! start_refiner; then
      stop_children
      if (( ONCE == 1 )); then
        die "Failed to start refiner_web."
      fi
    else
      log "Refiner is healthy. Logs:"
      log "  Refiner: ${REFINER_LOG}"
      wait "${REFINER_PID}" || true
      if (( SHUTTING_DOWN == 1 )); then
        exit 0
      fi
      if ! kill -0 "${REFINER_PID}" 2>/dev/null; then
        log "refiner_web exited unexpectedly."
      fi
      stop_children
      if (( ONCE == 1 )); then
        die "refiner_web exited."
      fi
    fi
  fi

  restart_count=$((restart_count + 1))
  if (( MAX_RESTARTS > 0 && restart_count > MAX_RESTARTS )); then
    die "Exceeded MAX_RESTARTS=${MAX_RESTARTS}."
  fi
  log "Restarting stack in ${RESTART_BACKOFF_SEC}s (attempt ${restart_count})..."
  sleep "${RESTART_BACKOFF_SEC}"
done
