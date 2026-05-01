"""
Basic best-practices evaluator for Confluence spaces.

This module encodes a small, extensible set of heuristics inspired by
agile/telco/security documentation hygiene. It intentionally avoids any
external dependencies and can be expanded later or fed by standards
documents. Output is a list of finding dicts with category, message, severity.
"""
from __future__ import annotations

from typing import Any, Dict, List
import datetime as dt


def _pct(n: int, d: int) -> float:
    return round((n / d * 100.0), 1) if d else 0.0


def evaluate_against_baselines(space: Dict[str, Any], pages: List[Any], metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    total = metrics.get("total_pages", 0)
    with_labels = metrics.get("pages_with_labels", 0)
    stale_180 = metrics.get("stale_pages_180d", 0)
    unknown_dates = metrics.get("unknown_update_dates", 0)

    # 1) Label hygiene: encourage taxonomy/labels for findability and governance
    if total:
        pct_labels = _pct(with_labels, total)
        if pct_labels < 60.0:
            findings.append({
                "category": "Information Architecture",
                "severity": "medium",
                "message": f"Only {pct_labels}% of pages have labels. Adopt a standard taxonomy (e.g., product, team, lifecycle) and label pages for discoverability and governance."
            })

    # 2) Freshness: avoid stale content (telco/security programs emphasize currency)
    if total:
        pct_stale = _pct(stale_180, total)
        if pct_stale > 25.0:
            findings.append({
                "category": "Content Freshness",
                "severity": "high",
                "message": f"{pct_stale}% of pages appear older than 180 days. Establish review cadences and page owners to reduce stale content."
            })

    if unknown_dates:
        findings.append({
            "category": "Metadata Quality",
            "severity": "low",
            "message": f"{unknown_dates} pages have unknown last-updated timestamps. Ensure pages are versioned and updated via Confluence editor to preserve metadata."
        })

    # 3) Ownership concentration: look for over-concentration in few authors
    top_authors = metrics.get("top_authors", [])
    if top_authors:
        top_total = sum(c for _, c in top_authors)
        if total and (top_total / total) > 0.7:
            findings.append({
                "category": "Ownership & Bus Factor",
                "severity": "medium",
                "message": "A large share of pages are authored by few people. Encourage broader contribution and assign clear page owners/maintainers."
            })

    # 4) Security hygiene reminders
    findings.append({
        "category": "Security Hygiene",
        "severity": "info",
        "message": "Ensure spaces use appropriate permissions; avoid storing secrets; link to secure repositories for credentials; add Data Classification labels to sensitive docs."
    })

    # 5) Agile best practices
    findings.append({
        "category": "Agile/SDLC Practices",
        "severity": "info",
        "message": "Consider standard page templates for RFCs/ADRs, runbooks, and post-incident reviews. Link Jira epics/stories to design docs and keep diagrams current."
    })

    return findings
