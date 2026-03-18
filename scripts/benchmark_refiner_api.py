#!/usr/bin/env python3
"""
Small load/latency benchmark helper for Refiner API endpoints.

Example:
  ./scripts/benchmark_refiner_api.py \
      --url http://127.0.0.1:5001/api/assistant/requirements \
      --method POST \
      --payload-file /tmp/assistant_payload.json \
      --requests 60 \
      --concurrency 8
"""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass
class Sample:
    latency_ms: float
    status_code: int
    ok: bool
    error: Optional[str] = None


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil((p / 100.0) * len(ordered)) - 1)))
    return ordered[idx]


def _load_payload(raw: Optional[str], file_path: Optional[str]) -> Optional[Dict[str, Any]]:
    if raw and file_path:
        raise ValueError("Use either --payload-json or --payload-file, not both.")
    if file_path:
        with open(file_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    if raw:
        return json.loads(raw)
    return None


def _single_request(
    url: str,
    method: str,
    payload: Optional[Dict[str, Any]],
    timeout: float,
    headers: Dict[str, str],
) -> Sample:
    started = time.perf_counter()
    try:
        resp = requests.request(
            method,
            url,
            json=payload if method in {"POST", "PUT", "PATCH"} else None,
            params=payload if method == "GET" else None,
            headers=headers or None,
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return Sample(
            latency_ms=elapsed_ms,
            status_code=resp.status_code,
            ok=200 <= resp.status_code < 400,
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return Sample(latency_ms=elapsed_ms, status_code=0, ok=False, error=str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Refiner API latency/throughput.")
    parser.add_argument("--url", required=True, help="Endpoint URL, e.g. http://127.0.0.1:5001/api/health")
    parser.add_argument("--method", default="POST", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    parser.add_argument("--payload-json", default="", help="Inline JSON payload string.")
    parser.add_argument("--payload-file", default="", help="Path to JSON payload file.")
    parser.add_argument("--requests", type=int, default=40, help="Total number of requests.")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent worker count.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout seconds.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="Optional request header in key:value form. Can be provided multiple times.",
    )
    args = parser.parse_args()

    total_requests = max(1, args.requests)
    workers = max(1, args.concurrency)
    payload = _load_payload(args.payload_json or None, args.payload_file or None)
    headers: Dict[str, str] = {}
    for item in args.header:
        if ":" not in item:
            raise ValueError(f"Invalid --header value: {item}")
        key, value = item.split(":", 1)
        headers[key.strip()] = value.strip()

    started = time.perf_counter()
    samples: List[Sample] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _single_request,
                args.url,
                args.method,
                payload,
                args.timeout,
                headers,
            )
            for _ in range(total_requests)
        ]
        for fut in as_completed(futures):
            samples.append(fut.result())
    total_elapsed_s = max(0.0001, time.perf_counter() - started)

    latencies = [sample.latency_ms for sample in samples]
    ok_count = sum(1 for sample in samples if sample.ok)
    err_count = total_requests - ok_count
    req_per_sec = total_requests / total_elapsed_s
    status_hist: Dict[int, int] = {}
    for sample in samples:
        status_hist[sample.status_code] = status_hist.get(sample.status_code, 0) + 1

    print("Benchmark summary")
    print(f"url={args.url} method={args.method} requests={total_requests} concurrency={workers}")
    print(f"elapsed_s={total_elapsed_s:.3f} throughput_rps={req_per_sec:.2f}")
    print(f"success={ok_count} failure={err_count} success_rate={ok_count / total_requests:.3f}")
    print(
        "latency_ms "
        f"p50={_percentile(latencies, 50):.2f} "
        f"p95={_percentile(latencies, 95):.2f} "
        f"p99={_percentile(latencies, 99):.2f} "
        f"max={max(latencies):.2f}"
    )
    print(f"status_hist={json.dumps(status_hist, sort_keys=True)}")
    if err_count:
        first_error = next((sample.error for sample in samples if sample.error), None)
        if first_error:
            print(f"first_error={first_error}")
    return 0 if err_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

