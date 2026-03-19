#!/usr/bin/env bash
set -euo pipefail

if ! command -v trivy >/dev/null 2>&1; then
  echo "trivy is required but not installed." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required but not installed." >&2
  exit 1
fi

IMAGE="${1:-ghcr.io/neuralmimicry/refiner:latest}"
OUT_FILE="${2:-refiner_security_feed.jsonl}"
SERVICE_NAME="${SERVICE_NAME:-refiner}"
SCANNER_NAME="${SCANNER_NAME:-trivy}"

tmp_json="$(mktemp)"
trap 'rm -f "$tmp_json"' EXIT

trivy image --quiet --format json "$IMAGE" > "$tmp_json"

jq -cr --arg image "$IMAGE" --arg service "$SERVICE_NAME" --arg scanner "$SCANNER_NAME" '
  .Results[]? as $result
  | ($result.Vulnerabilities // [])[]
  | {
      service: $service,
      image: $image,
      severity: ((.Severity // "unknown") | ascii_downcase),
      cvss: (.CVSS.nvd.V3Score // .CVSS.redhat.V3Score // .CVSS.ghsa.V3Score // 0),
      cve: (.VulnerabilityID // "unknown"),
      title: (.Title // .PkgName // "unknown finding"),
      scanner: $scanner,
      status: "open",
      finding_id: ((.VulnerabilityID // "unknown") + ":" + (.PkgName // "unknown"))
    }
' "$tmp_json" >> "$OUT_FILE"

echo "Wrote Tracey security feed entries to: $OUT_FILE"
