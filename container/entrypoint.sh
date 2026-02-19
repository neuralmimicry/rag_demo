#!/bin/sh
set -eu

mode="${1:-full}"
if [ "$#" -gt 0 ]; then
  shift
fi

if [ -z "${REFINER_PORT:-}" ] && [ -n "${PORT:-}" ]; then
  export REFINER_PORT="$PORT"
fi
if [ -z "${REFINER_FRONTEND_PORT:-}" ] && [ -n "${PORT:-}" ]; then
  export REFINER_FRONTEND_PORT="$PORT"
fi

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
    exec python refiner_web.py "$@"
    ;;
  frontend)
    exec python frontend_server.py "$@"
    ;;
  full|combined)
    exec python refiner_web.py "$@"
    ;;
  *)
    echo "Usage: $0 [backend|frontend|full] [args...]" >&2
    exit 1
    ;;
esac
