"""Configuration loading helpers shared across Refiner workflows."""

from __future__ import annotations

import json
from typing import Any, Dict


def load_config(path: str = "config.json") -> Dict[str, Any]:
    """Load config JSON with safe defaults and backward-compatible migration."""
    defaults: Dict[str, Any] = {
        "instances": [
            {
                "name": "VirginMediaO2 Ltd",
                "jira_url": "https://neuralmimicry.atlassian.net",
            }
        ],
        "llm_providers": [],
        "search_engines": [],
        "data_files": {
            "engineer_names": "engineer_names.csv",
            "leaderboard": "leaderboard.csv",
            "monthly_csv_prefix": "monthly_subtask_summary_data",
            "timelines": "timelines.csv",
            "gantt_projects": "gantt_projects.png",
            "gantt_html": "gantt_projects.html",
            "kpis_html": "kpis.html",
        },
        "issue_types": [
            "Bug",
            "Improvement",
            "New Feature",
            "Spike",
            "Epic",
            "Story",
            "Task",
            "Sub-task",
        ],
        "priority_ranking": {
            "Highest": 1,
            "High": 2,
            "Medium": 3,
            "Low": 4,
            "Lowest": 5,
        },
        "issue_ranking": {
            "Epic": 1,
            "Bug": 2,
            "Spike": 3,
            "New Feature": 4,
            "Improvement": 5,
            "Story": 6,
            "Task": 7,
            "Sub-task": 8,
        },
        "custom_fields": {
            "skills_field": "customfield_10900",
            "workstream_field": "customfield_10952",
            "universe_skill_name": "UniVerse",
            "priority_index_field": "customfield_10104",
        },
        "office_hours": {
            "start_hour": 9,
            "end_hour": 17,
            "country": "GB",
        },
        "search": {
            "prefer_client": True,
            "page_size": 100,
            "max_items": 10000,
        },
        "confluence": {
            "page_size": 100,
            "max_items": 10000,
            "comments_page_size": 100,
            "max_comments": 10000,
        },
        "ui": {
            "gantt_default_collapsed": {
                "projects": False,
                "epics": False,
            }
        },
    }

    try:
        with open(path, "r") as f:
            user_cfg = json.load(f)

        if "company" in user_cfg and "instances" not in user_cfg:
            user_cfg["instances"] = [user_cfg["company"]]

        for key, value in user_cfg.items():
            if isinstance(value, dict) and key in defaults and isinstance(defaults[key], dict):
                defaults[key].update(value)
            else:
                defaults[key] = value
    except Exception:
        pass

    if not defaults.get("instances"):
        defaults["instances"] = [
            {
                "name": "VirginMediaO2 Ltd",
                "jira_url": "https://neuralmimicry.atlassian.net",
            }
        ]

    return defaults
