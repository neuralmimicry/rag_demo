#!/bin/sh
set -eu

mode="${1:-full}"
if [ "$#" -gt 0 ]; then
  shift
fi

export REFINER_JOB_DIR="${REFINER_JOB_DIR:-/app/job_data}"
if ! mkdir -p "$REFINER_JOB_DIR" 2>/dev/null; then
  export REFINER_JOB_DIR="$(pwd)/job_data"
  mkdir -p "$REFINER_JOB_DIR"
fi

if [ -z "${REFINER_PORT:-}" ] && [ -n "${PORT:-}" ]; then
  export REFINER_PORT="$PORT"
fi
if [ -z "${REFINER_FRONTEND_PORT:-}" ] && [ -n "${PORT:-}" ]; then
  export REFINER_FRONTEND_PORT="$PORT"
fi

BACKEND_MODULE="${REFINER_BACKEND_MODULE:-refiner.refiner_web}"
FRONTEND_MODULE="${REFINER_FRONTEND_MODULE:-refiner.frontend_server}"
CLI_MODULE="${REFINER_CLI_MODULE:-refiner.run_refiner}"

gpu_status="not_detected"
gpu_source="none"
if command -v nvidia-smi >/dev/null 2>&1; then
  smi_out="$(nvidia-smi -L 2>/dev/null || true)"
  if [ -n "$smi_out" ]; then
    gpu_status="detected"
    gpu_source="nvidia-smi"
  fi
fi
if [ "$gpu_status" != "detected" ] && command -v clinfo >/dev/null 2>&1; then
  clinfo_out="$(clinfo -l 2>/dev/null || true)"
  if printf "%s" "$clinfo_out" | grep -q "Platform #"; then
    gpu_status="detected"
    gpu_source="clinfo"
  elif printf "%s" "$clinfo_out" | grep -q "Number of platforms: 0"; then
    gpu_status="not_detected"
    gpu_source="clinfo"
  else
    gpu_status="unknown"
    gpu_source="clinfo"
  fi
fi

echo "Refiner: GPU detection=${gpu_status} source=${gpu_source}"

case "$mode" in
  backend)
    exec python -m "$BACKEND_MODULE" "$@"
    ;;
  frontend)
    exec python -m "$FRONTEND_MODULE" "$@"
    ;;
  tests|test|suite)
    if [ ! -d tests ]; then
      echo "Tests are not bundled in this runtime image." >&2
      exit 1
    fi
    if [ "$#" -gt 0 ]; then
      exec pytest "$@"
    fi
    exec pytest tests
    ;;
  smoke)
    if [ -f refiner/refiner_web.py ] && [ -f refiner/run_refiner.py ]; then
      exec python -m py_compile refiner/refiner_web.py refiner/run_refiner.py "$@"
    fi
    exec python -c 'from refiner import refiner_web, run_refiner'
    ;;
  cli)
    exec python -m "$CLI_MODULE" "$@"
    ;;
  full|combined|stack)
    exec ./scripts/start_refiner_stack.sh "$@"
    ;;
  *)
    echo "Usage: $0 [backend|frontend|full|stack|tests|smoke|cli] [args...]" >&2
    exit 1
    ;;
esac
