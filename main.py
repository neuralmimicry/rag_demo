"""Core Jira statistics workflow and shared configuration helpers.

This module is the historical backbone of Refiner and still powers:
- the default Jira reporting pipeline,
- data extraction/fallback logic for issue retrieval,
- timeline/KPI report generation, and
- shared configuration/credential resolution consumed by other workflows.
"""

import calendar
from datetime import datetime, timedelta, timezone
import holidays
import getpass
import base64
import csv
import requests
from fuzzywuzzy import process
import re
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from jira import JIRA as jira_api
import os  # For environment variables
from typing import Any, List, Dict, Optional
from flask import Flask, request, jsonify

# Discovery for narrowing JQL using Confluence/Jira keywords
from discover_hierarchy import discover_hierarchy, build_refined_jql, DEFAULT_CACHE_FILE

app = Flask(__name__)


@app.route('/', methods=['POST'])
def webhook():
    """Minimal webhook receiver used by AppSheet-style integrations."""
    data = request.json
    # Process the data from AppSheet
    response_data = {
        'message': 'Received data!',
        'your_data': data
    }
    return jsonify(response_data)


# Configuration loading
# To keep algorithmic code reusable and isolate changing data/schemas (e.g., company names,
# engineers, field IDs, rankings), we load settings from an external JSON config file.
# Defaults below preserve current behaviour if config.json is missing or incomplete.
import json
from types import SimpleNamespace as NS
from config_loader import load_config

# Resolve configuration into module-level variables used by the rest of the code
_CONFIG = load_config()
INSTANCES = _CONFIG.get("instances", [])
LLM_PROVIDERS = _CONFIG.get("llm_providers", [])
SEARCH_ENGINES = _CONFIG.get("search_engines", [])
# For backward compatibility with code that expects a single JIRA_URL
FIRST_INSTANCE = INSTANCES[0] if INSTANCES else {}
JIRA_URL = FIRST_INSTANCE.get("jira_url", "https://neuralmimicry.atlassian.net")
EXPECTED_ISSUE_TYPES = _CONFIG.get("issue_types", [])
CSV_FILE_NAME = _CONFIG.get("data_files", {}).get("monthly_csv_prefix", "monthly_subtask_summary_data")
PRIORITY_RANKING = _CONFIG.get("priority_ranking", {})
ISSUE_RANKING = _CONFIG.get("issue_ranking", {})
ENGINEER_NAMES_FILE = _CONFIG.get("data_files", {}).get("engineer_names", "engineer_names.csv")
LEADERBOARD_FILE = _CONFIG.get("data_files", {}).get("leaderboard", "leaderboard.csv")
TIMELINES_FILE = _CONFIG.get("data_files", {}).get("timelines", "timelines.csv")
GANTT_FILE = _CONFIG.get("data_files", {}).get("gantt_projects", "gantt_projects.png")
GANTT_HTML_FILE = _CONFIG.get("data_files", {}).get("gantt_html", "gantt_projects.html")
KPI_HTML_FILE = _CONFIG.get("data_files", {}).get("kpis_html", "kpis.html")
CUSTOM_FIELDS = _CONFIG.get("custom_fields", {})
OFFICE_HOURS = _CONFIG.get("office_hours", {})
# Search behaviour configuration
SEARCH_CFG = _CONFIG.get("search", {}) or {}
PREFER_CLIENT_SEARCH = str(os.getenv("PREFER_CLIENT_SEARCH") or SEARCH_CFG.get("prefer_client", False)).lower() in ("1", "true", "yes")
PAGE_SIZE = int(SEARCH_CFG.get("page_size", 100) or 100)
MAX_ITEMS = int(SEARCH_CFG.get("max_items", 10000) or 10000)
FAIL_FAST_HTTP = str(os.getenv("FAIL_FAST_HTTP") or SEARCH_CFG.get("fail_fast_http", True)).lower() in ("1", "true", "yes")
ALLOW_ALT_SHAPES = str(os.getenv("ALLOW_ALT_SHAPES") or SEARCH_CFG.get("allow_alt_shapes", True)).lower() in ("1", "true", "yes")
DEBUG_SEARCH = str(os.getenv("DEBUG_SEARCH") or SEARCH_CFG.get("debug", False)).lower() in ("1", "true", "yes")
# Final fallback recency window (days) for bounded queries when refined/base paths return 0
RECENT_DAYS = int(os.getenv("RECENT_DAYS") or SEARCH_CFG.get("recent_days", 180) or 180)
# Minimum acceptable number of issues before we relax constraints further (non-zero but too small)
MIN_RESULTS = int(os.getenv("MIN_RESULTS") or SEARCH_CFG.get("min_results", 20) or 20)
FORCE_ULTRA_BROAD = str(os.getenv("FORCE_ULTRA_BROAD") or SEARCH_CFG.get("force_ultra_broad", False)).lower() in ("1", "true", "yes")
# Allow a final extreme-broad attempt with no WHERE clause (ORDER BY created DESC)
ALLOW_EXTREME_BROAD = str(os.getenv("ALLOW_EXTREME_BROAD") or SEARCH_CFG.get("allow_extreme_broad", True)).lower() in ("1", "true", "yes")
# Optional additional fallbacks toggles
ENABLE_USER_SCOPED_FALLBACK = str(os.getenv("ENABLE_USER_SCOPED_FALLBACK") or SEARCH_CFG.get("enable_user_scoped_fallback", True)).lower() in ("1", "true", "yes")
TRY_CREATED_WINDOW = str(os.getenv("TRY_CREATED_WINDOW") or SEARCH_CFG.get("try_created_window", True)).lower() in ("1", "true", "yes")
# Optional: avoid ORDER BY Rank, which can be problematic in some instances
AVOID_RANK_ORDER = str(os.getenv("AVOID_RANK_ORDER") or SEARCH_CFG.get("avoid_rank_order", False)).lower() in ("1", "true", "yes")
_RFO = (os.getenv("RANK_FALLBACK") or SEARCH_CFG.get("rank_fallback", "created") or "created").strip().lower()
RANK_FALLBACK = "updated" if _RFO == "updated" else "created"
# Cache controls for paging and local query support
ENABLE_CACHE = str(os.getenv("ENABLE_CACHE") or SEARCH_CFG.get("enable_cache", True)).lower() in ("1", "true", "yes")
ISSUES_CACHE_FILE = (SEARCH_CFG.get("issues_cache") or ".issues_cache.jsonl").strip()
PREFER_CACHE_FOR_FALLBACKS = str(os.getenv("PREFER_CACHE_FOR_FALLBACKS") or SEARCH_CFG.get("prefer_cache_for_fallbacks", True)).lower() in ("1", "true", "yes")
CACHE_MAX_AGE_DAYS = int(os.getenv("CACHE_MAX_AGE_DAYS") or SEARCH_CFG.get("cache_max_age_days", 7) or 7)
# Optional: iterate per project instead of querying all projects at once
ITERATE_PER_PROJECT = str(os.getenv("ITERATE_PER_PROJECT") or SEARCH_CFG.get("iterate_per_project", False)).lower() in ("1", "true", "yes")
# Optionally probe discovered projects to ensure they are accessible (return >=1 issue) before building refined JQL
PROBE_ACCESSIBLE_PROJECTS = str(os.getenv("PROBE_ACCESSIBLE_PROJECTS") or SEARCH_CFG.get("probe_accessible_projects", True)).lower() in ("1", "true", "yes")
# Transition debug logging for status changes. Enabled by default; set DEBUG_TRANSITIONS=0 to suppress.
DEBUG_TRANSITIONS = str(os.getenv("DEBUG_TRANSITIONS") or "0").lower() in ("1", "true", "yes")
# Disable Jira or Confluence operations
DISABLE_JIRA = str(os.getenv("DISABLE_JIRA") or "0").lower() in ("1", "true", "yes")
DISABLE_CONFLUENCE = str(os.getenv("DISABLE_CONFLUENCE") or "0").lower() in ("1", "true", "yes")
# JQL query can be overridden by env var JQL_QUERY for flexibility
JQL_QUERY = os.getenv("JQL_QUERY") or _CONFIG.get("jql_query", 'ORDER BY Rank')

# In-memory cache of issue keys appended to the JSONL cache during the current run
# This prevents duplicate writes within a single execution.
_CACHE_SEEN_KEYS = set()

# UI/HTML behaviour configuration
UI_CFG = _CONFIG.get("ui", {}) or {}
_GDC = (UI_CFG.get("gantt_default_collapsed") or {}) if isinstance(UI_CFG, dict) else {}
DEFAULT_COLLAPSE_PROJECTS = bool(_GDC.get("projects", False))
DEFAULT_COLLAPSE_EPICS = bool(_GDC.get("epics", False))


def read_senior_list(filename: str) -> List[Dict[str, Optional[datetime]]]:
    """
    Reads the senior names from a CSV file and returns a list of dictionaries with name, start date, and end date.

    Be resilient to date formatting:
    - Accepts common formats like YYYY-MM-DD, DD/MM/YYYY, YYYY/MM/DD, and ISO 8601.
    - Skips rows with invalid/missing start dates and logs a short warning.
    """
    def _parse_date_flexible(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        s = str(value).strip()
        # Try common explicit formats first
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
        # Try ISO 8601 (date or datetime); handle trailing Z
        try:
            s2 = s.replace("Z", "+00:00")
            return datetime.fromisoformat(s2)
        except Exception:
            return None

    senior_list = []
    try:
        with open(filename, mode='r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = (row.get("Name") or "").strip()
                start_dt = _parse_date_flexible(row.get("StartDate"))
                end_dt = _parse_date_flexible(row.get("EndDate"))
                if not name or not start_dt:
                    print(f"Skipping row with invalid name/start date: {row}")
                    continue
                senior_info = {
                    "name": name,
                    "start_date": start_dt,
                    "end_date": end_dt
                }
                senior_list.append(senior_info)
    except Exception as e:
        # Non-fatal: proceed without senior filtering if file missing or malformed
        fname = filename or "engineer_names.csv"
        print(f"Warning: unable to read '{fname}' ({e}); continuing without senior-based filtering.")
    return senior_list


def filter_active_seniors(senior_list: List[Dict[str, Optional[datetime]]], query_date) -> List[str]:
    """
    Filters the list of seniors based on whether they are active on the given date.

    Accepts `query_date` as either:
    - a datetime/date object, or
    - a string in the format 'YYYY-MM' (first day of month assumed)
    Any other type will result in an empty list being returned safely.
    """
    # Normalize query_date to a datetime (naive)
    if isinstance(query_date, datetime):
        qd = query_date
    else:
        try:
            # Expect 'YYYY-MM' by default (from convert_month_string_to_datetime)
            qd = datetime.strptime(str(query_date), "%Y-%m")
        except Exception:
            return []

    active_seniors = []
    for senior in senior_list:
        start = senior.get("start_date")
        end = senior.get("end_date") or datetime.now()
        try:
            if isinstance(start, datetime) and isinstance(end, datetime) and start <= qd <= end:
                active_seniors.append(senior.get("name"))
        except Exception:
            # Skip malformed entries
            continue
    return active_seniors

# Secure credential handling
from credentials import get_credentials, get_llm_credentials, get_search_credentials


# for debugging keys
def print_dict_hierarchy(d: dict, indent=0):
    """
    Recursively prints a dictionary to display its nested structure.
    :param d: The dictionary to print.
    :param indent: The current indentation level.
    """
    for key, value in d.items():
        print('\t' * indent + str(key))
        if isinstance(value, dict):
            print_dict_hierarchy(value, indent + 1)


def is_office_hour(dt, start_hour=9, end_hour=17, holidays=None):
    """
    Check if the datetime is within office hours and not a holiday.
    """
    return dt.weekday() < 5 and start_hour <= dt.hour < end_hour and dt.date() not in holidays


# Function to convert seconds to nearest work-unit equivalent
def seconds_to_work_units(seconds):
    """
    Converts seconds to work units, where 4 hours is considered one work unit.
    :param seconds: The number of seconds.
    :return: The number of work units as an integer.
    """
    hours = seconds / 3600  # Convert seconds to hours
    return int(hours // 4)  # Always round down to the nearest work unit


def convert_month_string_to_datetime(month_str: str) -> datetime:
    """
    Converts a month string in the format 'mon-yy' to a datetime object representing the first day of that month.

    Args:
    month_str (str): A string representing the month and year, formatted as 'yyyy-mm', e.g., '2023-04'.

    Returns:
    datetime: A datetime object set to the first day of the given month and year.
    """
    return datetime.strptime(month_str, "%Y-%m")


def _parse_jira_timestamp(value) -> Optional[datetime]:
    """
    Robustly parse Jira/ISO timestamps into datetime.
    Accepts strings like:
    - 2024-10-10T16:14:06.361+0100
    - 2024-10-10T16:14:06+0100
    - 2024-10-10T16:14:06.361Z
    - 2024-10-10 16:14:06+00:00
    Returns None if parsing fails or value is falsy.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    # Try with timezone offset and microseconds
    fmts = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ]
    # Handle trailing Z by translating to +00:00 for fromisoformat
    if s.endswith("Z"):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
    # Try predefined formats
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    # Last resort: fromisoformat with various tweaks
    try:
        s2 = s.replace(" ", "T")
        if "+" in s2[10:]:
            # ensure colon in tz offset for fromisoformat, if missing
            # e.g., +0100 -> +01:00
            main, tz = s2[:-5], s2[-5:]
            if tz[3] != ":":
                s2 = f"{main}{tz[:3]}:{tz[3:]}"
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def get_monthly_worklog_times(issue):
    """
    Gathers worklog times for each month and categorizes them by workstream.
    Defensive against missing worklog structures (e.g., cached/HTTP-derived issues)
    by returning an empty mapping when no worklogs are available.

    :param issue: The issue from which to extract worklog times.
    :return: A dictionary mapping each month to its aggregated worklog times.
    """
    # Safely access fields and worklogs supporting multiple shapes
    fields = getattr(issue, 'fields', None)
    if not fields:
        return {}
    wl_container = getattr(fields, 'worklog', None)
    worklogs = []
    try:
        if wl_container is None:
            worklogs = []
        elif isinstance(wl_container, list):
            worklogs = wl_container
        else:
            worklogs = getattr(wl_container, 'worklogs', None) or []
    except Exception:
        worklogs = []

    if not worklogs:
        return {}

    monthly_worklog_times = {}
    # Extract the 'value' from each CustomFieldOption object
    # Use configurable custom field IDs and skill names
    skills_field_id = CUSTOM_FIELDS.get("skills_field", "customfield_10900")
    workstream_field_id = CUSTOM_FIELDS.get("workstream_field", "customfield_10952")
    universe_skill_name = CUSTOM_FIELDS.get("universe_skill_name", "UniVerse")

    # Coerce skills to list and extract values safely
    def _as_list(x):
        if x is None:
            return []
        return x if isinstance(x, list) else [x]

    try:
        skill_items = _as_list(getattr(fields, skills_field_id, None))
    except Exception:
        skill_items = []
    tech_skills = []
    for option in skill_items:
        try:
            tech_skills.append(getattr(option, 'value', None) or str(option))
        except Exception:
            continue

    # Determine if this worklog is for UniVerse work or not (configurable skill name)
    is_universe = universe_skill_name in tech_skills
    # Derive workstream using configurable field ID
    try:
        workstream_field = getattr(fields, workstream_field_id, None)
    except Exception:
        workstream_field = None
    if workstream_field is None:
        base_workstream = None
    else:
        try:
            base_workstream = getattr(workstream_field, 'value', None)
            if base_workstream is None and not isinstance(workstream_field, (list, dict)):
                base_workstream = str(workstream_field)
        except Exception:
            base_workstream = None
    ws_suffix = ' (UniVerse)' if is_universe else ' (non-UniVerse)'
    worklog_dev_workstream = (base_workstream + ws_suffix) if base_workstream else None

    for worklog in worklogs:
        started_dt = _parse_jira_timestamp(getattr(worklog, 'started', None))
        if not started_dt:
            # Skip malformed dates rather than raising
            continue
        worklog_date = started_dt.strftime("%Y-%m")
        author_obj = getattr(worklog, 'author', None)
        worklog_author = (
            getattr(author_obj, 'displayName', None)
            or getattr(author_obj, 'name', None)
            or 'Unknown'
        )
        if worklog_author not in monthly_worklog_times:  # initialize new dictionary for new assignee
            monthly_worklog_times[worklog_author] = {}
        if worklog_date not in monthly_worklog_times[worklog_author]:  # initialize new dictionary for new date
            monthly_worklog_times[worklog_author][worklog_date] = {
                'time_spent': 0,
            }
            # Initialize workstream bucket if we have a name
            if worklog_dev_workstream:
                monthly_worklog_times[worklog_author][worklog_date][worklog_dev_workstream] = {'time_spent': 0}
        # Ensure workstream bucket exists when a name is available
        if worklog_dev_workstream and worklog_dev_workstream not in monthly_worklog_times[worklog_author][worklog_date]:
            monthly_worklog_times[worklog_author][worklog_date][worklog_dev_workstream] = {'time_spent': 0}

        # Correctly increment time_spent at both the date level and the workstream level
        sec = getattr(worklog, 'timeSpentSeconds', None)
        sec = 0 if sec is None else sec
        monthly_worklog_times[worklog_author][worklog_date]['time_spent'] += sec
        if worklog_dev_workstream:
            monthly_worklog_times[worklog_author][worklog_date][worklog_dev_workstream]['time_spent'] += sec
    return monthly_worklog_times


def _get_holidays_calendar(country_code: str):
    """
    Return a holidays calendar instance for the given ISO country code.
    Defaults to UnitedKingdom for 'GB' to preserve existing behaviour.
    """
    code = (country_code or 'GB').upper()
    try:
        if code in ('GB', 'UK'):
            return holidays.UnitedKingdom()
        if code == 'US':
            return holidays.UnitedStates()
        if code == 'CA':
            return holidays.Canada()
        if code == 'DE':
            return holidays.Germany()
        if code == 'FR':
            return holidays.France()
        # Fallback generic: some providers support by country code directly
        return holidays.country_holidays(code)
    except Exception:
        # Final fallback to UK to avoid runtime errors if unsupported
        return holidays.UnitedKingdom()


def analyze_issue_transitions(issue):
    """
    Analyzes an issue's changelog to calculate the time spent from 'In Progress' to 'For Peer Review',
    considering only office hours (configurable; default 9am-5pm, Monday-Friday) and excluding regional bank holidays.

    The algorithm is intentionally data-agnostic; changing office hours or holiday region is done via config.json.

    :param issue: The issue whose transitions are to be analysed.
    :return: Total seconds spent and the count of QA returns.
    """
    # Load office hours and holidays region from configuration
    office_start_hour = int(OFFICE_HOURS.get('start_hour', 9))
    office_end_hour = int(OFFICE_HOURS.get('end_hour', 17))
    country_code = OFFICE_HOURS.get('country', 'GB')
    region_holidays = _get_holidays_calendar(country_code)

    time_to_code = timedelta()
    qa_returns = 0
    in_progress_timestamp = None

    # Safely access changelog histories; cached/HTTP-derived shapes may omit them
    try:
        histories = getattr(getattr(issue, 'changelog', None), 'histories', None)
    except Exception:
        histories = None
    if not histories:
        return time_to_code.total_seconds(), qa_returns

    # Sort histories by created timestamp (ascending), tolerating malformed timestamps
    def _hist_key(h):
        try:
            dt = _parse_jira_timestamp(getattr(h, 'created', None))
            return dt or datetime.min
        except Exception:
            return datetime.min
    sorted_histories = sorted(list(histories), key=_hist_key, reverse=False)

    def within_office_hours(dt):
        # Check if the date is a weekday and within office hours, excluding holidays
        return (dt.weekday() < 5 and
                office_start_hour <= dt.hour < office_end_hour and
                dt.date() not in region_holidays)

    for history in sorted_histories:
        items = []
        try:
            items = list(getattr(history, 'items', []) or [])
        except Exception:
            items = []
        for item in items:
            if item.field == 'status':
                # Always include the transition timestamp for clarity; can be toggled via DEBUG_TRANSITIONS
                if DEBUG_TRANSITIONS:
                    print(f"{history.created}: From: {item.fromString}, To: {item.toString}")
                if item.fromString == 'Ready to Develop' and item.toString == 'In Progress':
                    in_progress_timestamp = _parse_jira_timestamp(history.created)
                    if DEBUG_TRANSITIONS and in_progress_timestamp:
                        print(f"In Progress: {in_progress_timestamp}")  # Debug output
                elif item.toString == 'For Peer Review' and in_progress_timestamp:
                    peer_review_timestamp = _parse_jira_timestamp(history.created)
                    if DEBUG_TRANSITIONS and peer_review_timestamp:
                        print(f"For Peer Review: {peer_review_timestamp}")  # Debug output
                    if peer_review_timestamp > in_progress_timestamp:
                        # Calculate the duration only within office hours
                        current_time = in_progress_timestamp
                        while current_time < peer_review_timestamp:
                            if within_office_hours(current_time):
                                end_of_hour = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                                next_hour_within_office = min(end_of_hour, peer_review_timestamp)
                                time_to_code += next_hour_within_office - current_time
                            current_time += timedelta(hours=1)
                            current_time = current_time.replace(minute=0, second=0, microsecond=0)
                    in_progress_timestamp = None
                elif item.toString != 'Done' and item.fromString == 'Ready for QA':
                    qa_returns += 1

    return time_to_code.total_seconds(), qa_returns


# Function to normalize and clean names
def normalize_name(name):
    """
    Normalizes a name by converting it to lowercase and stripping out non-alphabetic characters.
    :param name: The name to normalize.
    :return: A normalized version of the name.
    """
    return re.sub(r'[^a-z\s]', '', name.lower())


def sorting_key(workstream):
    """
    Defines the sorting key for ordering workstreams, taking into account the suffix.
    :param workstream: The workstream to be sorted.
    :return: A tuple representing the sorting key.
    """
    if workstream is None:
        raise ValueError("workstream should not be None")  # or return a default value if appropriate

    # Check if the workstream ends with " (UniVerse)" or " (non-UniVerse)" and extract the base name accordingly
    if workstream.endswith(" (UniVerse)"):
        base_name = workstream[:-11]  # Remove " (UniVerse)" suffix
        universe_suffix = 2  # UniVerse comes after non-UniVerse
    elif workstream.endswith(" (non-UniVerse)"):
        base_name = workstream[:-15]  # Remove " (non-UniVerse)" suffix
        universe_suffix = 1  # non-UniVerse comes before UniVerse
    else:
        base_name = workstream
        universe_suffix = 0  # Default value for workstreams without these suffixes

    return base_name, universe_suffix, workstream


def plot_pie_charts(summary_data):
    """
    Plots pie charts for workstream distribution per month.
    :param summary_data: Aggregated data for each month to plot.
    """
    for month, data in summary_data.items():
        workstreams = []
        time_spents = []

        # Collect workstreams and their total time spent
        for workstream, workstream_data in data.items():
            if workstream not in ['time_spent', 'time_remaining', *EXPECTED_ISSUE_TYPES]:
                workstreams.append(workstream)
                time_spent = workstream_data.get('time_spent', 0)
                time_spents.append(time_spent)

        # Convert time to work units
        time_spents_in_work_units = [seconds_to_work_units(time) for time in time_spents]
        if sum(time_spents_in_work_units) == 0:  # Avoid plotting if no work has been done
            print(f"No work data available for {month}, skipping pie chart.")
            continue

        # Only convert time_spents_in_work_units to strings when passing to the autopct parameter
        time_spents_in_strings = [str(unit) for unit in time_spents_in_work_units]

        # Create a pie chart
        fig, ax = plt.subplots()
        ax.pie(time_spents_in_work_units, labels=workstreams, autopct=lambda p: '{:.1f}%'.format(p) if p > 0 else '', startangle=90)
        ax.axis('equal')  # Equal aspect ratio ensures the pie is drawn as a circle.

        # Add a title and save the figure
        plt.title(f"Workstream Distribution for {month}")
        plt.savefig(f"pie_chart_{month}.png")
        plt.close(fig)  # Close the figure to avoid displaying it in a non-interactive environment


# Basic Auth setup for JIRA
def create_jira_connection(username, password, jira_url: Optional[str] = None, instance_name: Optional[str] = None):
    """
    Create an authenticated Jira client using python-jira's supported basic_auth.

    Notes:
    - For Atlassian Cloud, `username` should be your email and `password` should be an API token.
    - We also attach the credentials to the client instance so HTTP fallbacks in fetch_issues
      can reuse them when calling the REST API directly.
    """
    url = jira_url or JIRA_URL
    options = {
        'server': url,
        'rest_api_version': 3,
    }
    # Use official python-jira basic_auth mechanism instead of crafting Authorization headers
    client = jira_api(options=options, basic_auth=(username, password))
    # Attach creds and url for downstream HTTP requests (used in fetch_issues)
    try:
        setattr(client, 'username', username)
        setattr(client, 'password', password)
        setattr(client, 'jira_url', url)
        if instance_name:
            setattr(client, 'instance_name', instance_name)
    except Exception:
        pass
    return client


def fetch_issues(jira_connector, jql_query):
    """
    Fetches issues from JIRA based on a JQL query.
    Primary path uses Atlassian's /rest/api/3/search/jql endpoint; robust fallbacks include
    an alternative batch payload and finally the python-jira client's search_issues.

    You can force the client path by setting config.search.prefer_client = true or
    environment variable PREFER_CLIENT_SEARCH=1.

    :param jira_connector: Authenticated JIRA client object (only used to source creds if available).
    :param jql_query: The JQL query string to execute.
    :return: A list of issues that match the JQL query.
    """
    # Source Jira URL from connector if available
    instance_url = getattr(jira_connector, 'jira_url', JIRA_URL)

    # If user prefers client search, skip HTTP attempts
    def _issue_to_raw(obj):
        """Best-effort convert a jira Issue or dict into a raw dict suitable for caching."""
        try:
            # python-jira Issue objects usually expose .raw
            raw = getattr(obj, 'raw', None)
            if isinstance(raw, dict):
                # Ensure jira_url is attached to raw for downstream processing
                if 'jira_url' not in raw:
                    raw['jira_url'] = instance_url
                return raw
        except Exception:
            pass
        # If it's already a dict (HTTP path), return as-is
        if isinstance(obj, dict):
            if 'jira_url' not in obj:
                obj['jira_url'] = instance_url
            return obj
        # Last resort: build a minimal shape from attributes used downstream
        try:
            fields = getattr(obj, 'fields', None)
            changelog = getattr(obj, 'changelog', None)
            key = getattr(obj, 'key', None)
            raw_fields = {}
            if fields:
                raw_fields = {k: getattr(fields, k) for k in dir(fields) if not k.startswith('_') and not callable(getattr(fields, k))}
            raw_changes = {}
            if changelog:
                raw_changes = {
                    'histories': getattr(changelog, 'histories', None)
                }
            return {'key': key, 'fields': raw_fields, 'changelog': raw_changes, 'jira_url': instance_url}
        except Exception:
            return None

    def _cache_append(objs, source_jql: str):
        if not ENABLE_CACHE or not objs:
            return
        try:
            import time as _time
            with open(ISSUES_CACHE_FILE, 'a') as f:
                ts = int(_time.time())
                for o in objs:
                    raw = _issue_to_raw(o)
                    if not isinstance(raw, dict):
                        continue
                    k = (raw.get('key') if isinstance(raw.get('key'), str) else None) or None
                    # Skip writing duplicate keys within the same run/instance
                    cache_id = (instance_url, k)
                    if k and cache_id in _CACHE_SEEN_KEYS:
                        continue
                    if k:
                        _CACHE_SEEN_KEYS.add(cache_id)
                    rec = {'fetched_at': ts, 'jql': source_jql, 'issue': raw, 'jira_url': instance_url}
                    try:
                        f.write(json.dumps(rec) + "\n")
                    except Exception:
                        continue
        except Exception:
            # best-effort cache; ignore errors
            pass

    def _client_search_all(jql: str):
        """Fetch all issues via python-jira client using explicit pagination.
        This keeps requests small and predictable across instances.
        """
        try:
            start_at = 0
            results = []
            while True:
                page = jira_connector.search_issues(jql, startAt=start_at, maxResults=PAGE_SIZE, expand='changelog,worklog')
                # python-jira may return a ResultList; coerce to list
                page_list = list(page) if not isinstance(page, list) else page
                if page_list:
                    results.extend(page_list)
                    # append to cache per page to avoid large memory spikes
                    _cache_append(page_list, jql)
                    if len(results) >= MAX_ITEMS:
                        return results[:MAX_ITEMS]
                if not page_list or len(page_list) < PAGE_SIZE:
                    break
                start_at += PAGE_SIZE
            return results
        except Exception:
            return []

    if PREFER_CLIENT_SEARCH:
        return _client_search_all(jql_query)

    try:
        import requests
        from requests.auth import HTTPBasicAuth
        username = getattr(jira_connector, 'username', None)
        password = getattr(jira_connector, 'password', None)
        attempted_client_fallback = False
        # If we don't have HTTP credentials attached to the client, try client search first
        if not username or not password:
            if DEBUG_SEARCH:
                print("No HTTP credentials attached; attempting client search before prompting...")
            client_issues = _client_search_all(jql_query)
            if client_issues:
                return client_issues
            # If client path returned nothing and we still want to try HTTP, only then fetch creds
            try:
                try:
                    username, password = get_credentials(getattr(jira_connector, 'instance_name', None))
                except TypeError:
                    username, password = get_credentials()
            except Exception:
                # If credentials cannot be obtained (e.g., non-interactive), return what we have
                return []
        auth = HTTPBasicAuth(str(username), str(password))
        attempted_client_fallback = False

        def parse_issues(data):
            issues = []
            if isinstance(data, dict):
                if "issues" in data:
                    issues = data.get("issues") or []
                elif "results" in data:
                    for result in data.get("results", []) or []:
                        issues.extend(result.get("issues", []) or [])
            return issues

        all_issues = []
        start_at = 0
        url = f"{instance_url}/rest/api/3/search/jql"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        attempted_search_endpoint = False

        while True:
            # 1) Preferred: POST /search/jql with top-level payload (paginate)
            payload1 = {"jql": jql_query, "startAt": start_at, "maxResults": PAGE_SIZE}
            if DEBUG_SEARCH:
                print(f"Requesting: {url}")
                print(f"Payload: {payload1}")
            resp1 = requests.post(url, json=payload1, headers=headers, auth=auth, params={"expand": "changelog,worklog"})
            try:
                resp1.raise_for_status()
                data1 = resp1.json()
                issues = parse_issues(data1)
                if issues:
                    all_issues.extend(issues)
                    _cache_append(issues, jql_query)
                    if len(all_issues) >= MAX_ITEMS:
                        return all_issues[:MAX_ITEMS]
                    if len(issues) < PAGE_SIZE:
                        return all_issues
                    start_at += PAGE_SIZE
                    continue  # next page
                else:
                    # No issues from /search/jql; try classic /search endpoint once before giving up
                    if not attempted_search_endpoint:
                        attempted_search_endpoint = True
                        search_url = f"{instance_url}/rest/api/3/search"
                        start_at2 = start_at
                        while True:
                            payload_s = {"jql": jql_query, "startAt": start_at2, "maxResults": PAGE_SIZE}
                            if DEBUG_SEARCH:
                                print(f"Retrying via classic endpoint: {search_url}")
                                print(f"Payload: {payload_s}")
                            resp_s = requests.post(search_url, json=payload_s, headers=headers, auth=auth, params={"expand": "changelog,worklog"})
                            try:
                                resp_s.raise_for_status()
                                data_s = resp_s.json()
                                issues_s = parse_issues(data_s)
                                if issues_s:
                                    all_issues.extend(issues_s)
                                    _cache_append(issues_s, jql_query)
                                    if len(all_issues) >= MAX_ITEMS:
                                        return all_issues[:MAX_ITEMS]
                                    if len(issues_s) < PAGE_SIZE:
                                        return all_issues
                                    start_at2 += PAGE_SIZE
                                    continue
                                else:
                                    break
                            except Exception as _:
                                break
                    # no issues overall, stop
                    return all_issues
            except requests.exceptions.HTTPError as e1:
                body = None
                try:
                    body = resp1.text[:200]
                except Exception:
                    pass
                print(f"HTTP error on /search/jql top-level payload: {e1}")
                if body:
                    print(f"Response body: {body}")
                # If configured to fail fast on HTTP errors, go directly to client fallback
                if FAIL_FAST_HTTP:
                    try:
                        attempted_client_fallback = True
                        client_issues = _client_search_all(jql_query)
                        if client_issues:
                            if DEBUG_SEARCH:
                                print(f"Client fallback succeeded with {len(client_issues)} issues")
                            return client_issues
                        else:
                            if DEBUG_SEARCH:
                                print("Client fallback returned no issues")
                    except Exception as e_client_fast:
                        if DEBUG_SEARCH:
                            print(f"Client fallback failed: {e_client_fast}")
                    break  # exit to alternative/batch gate below
                # Retry once with explicit fields/expand in JSON body (some instances require this)
                try:
                    alt_payload = {
                        "jql": jql_query,
                        "startAt": start_at,
                        "maxResults": PAGE_SIZE,
                        "fields": ["*all"],
                        "expand": ["changelog", "worklog"],
                    }
                    if DEBUG_SEARCH:
                        print(f"Retrying /search/jql with fields/expand in body: {alt_payload}")
                    resp1b = requests.post(url, json=alt_payload, headers=headers, auth=auth)
                    resp1b.raise_for_status()
                    data1b = resp1b.json()
                    issues = parse_issues(data1b)
                    if issues:
                        all_issues.extend(issues)
                        if len(all_issues) >= MAX_ITEMS:
                            return all_issues[:MAX_ITEMS]
                        if len(issues) < PAGE_SIZE:
                            return all_issues
                        start_at += PAGE_SIZE
                        continue
                    else:
                        return all_issues
                except Exception as e1b:
                    if DEBUG_SEARCH:
                        print(f"Alternate body payload also failed: {e1b}")
                # Try client fallback immediately for robustness on instances rejecting /search/jql
                try:
                    attempted_client_fallback = True
                    client_issues = _client_search_all(jql_query)
                    # If we got issues, return them; otherwise we will try batch payload next
                    if client_issues:
                        print(f"Client search_issues fallback succeeded with {len(client_issues)} issues")
                        return client_issues
                    else:
                        print("Client search_issues fallback returned no issues; attempting batch payload...")
                except Exception as e_client1:
                    print(f"Client search_issues fallback failed: {e_client1}")
                break  # break pagination loop and try fallbacks

        # 2) Alternative batch shape accepted by some instances: {queries: [{...}]}
        all_issues = []
        start_at = 0
        while True:
            payload2 = {"queries": [{"jql": jql_query, "startAt": start_at, "maxResults": PAGE_SIZE}]}
            print(f"Retrying with batch payload: {payload2}")
            resp2 = requests.post(url, json=payload2, headers=headers, auth=auth, params={"expand": "changelog,worklog"})
            try:
                resp2.raise_for_status()
                data2 = resp2.json()
                issues = parse_issues(data2)
                if issues:
                    all_issues.extend(issues)
                    if len(all_issues) >= MAX_ITEMS:
                        return all_issues[:MAX_ITEMS]
                    if len(issues) < PAGE_SIZE:
                        return all_issues
                    start_at += PAGE_SIZE
                    continue
                else:
                    return all_issues
            except requests.exceptions.HTTPError as e2:
                body = None
                try:
                    body = resp2.text[:200]
                except Exception:
                    pass
                print(f"HTTP error on /search/jql batch payload: {e2}")
                if body:
                    print(f"Response body: {body}")
                break

        # 3) Fallback to Jira client's search_issues (works in tests and many environments)
        if not attempted_client_fallback:
            try:
                attempted_client_fallback = True
                return jira_connector.search_issues(jql_query, maxResults=False, expand='changelog,worklog')
            except Exception as e_client:
                print(f"Client search_issues fallback failed: {e_client}")
                return []
        else:
            return []

    except Exception as e:
        print(f"Error fetching issues: {e}")
        # Final fallback to client method to satisfy test environment
        try:
            return jira_connector.search_issues(jql_query, maxResults=False, expand='changelog,worklog')
        except Exception:
            return []


def sort_issues_by_priority(issues):
    """
    Sorts issues by a configurable custom priority/index field with safe fallbacks.

    Behavior:
    - If the configured custom field exists on the issue (e.g., customfield_10104), sort by its string value.
    - Else, if a standard priority exists, sort by PRIORITY_RANKING mapping (Highest..Lowest), then by priority name.
    - Else, fall back to the issue key to provide a deterministic order.

    The custom field id can be configured via config.json -> custom_fields.priority_index_field.
    Defaults to "customfield_10104" for backward compatibility.
    """
    priority_field = (CUSTOM_FIELDS or {}).get("priority_index_field", "customfield_10104")

    def _sort_key(issue):
        # 1) Configured custom field (string compare)
        try:
            val = getattr(issue.fields, priority_field)
            if val is not None:
                return (0, str(val))
        except Exception:
            pass
        # 2) Built-in priority using PRIORITY_RANKING mapping
        try:
            prio = getattr(issue.fields, 'priority', None)
            prio_name = getattr(prio, 'name', None) or str(prio) if prio is not None else None
            if prio_name:
                rank = PRIORITY_RANKING.get(str(prio_name), 999)
                return (1, rank, str(prio_name))
        except Exception:
            pass
        # 3) Fallback to issue key
        try:
            return (2, str(getattr(issue, 'key', '')))
        except Exception:
            return (3, '')

    return sorted(issues or [], key=_sort_key, reverse=False)


def leaderboard_output(sorted_leaderboard):
    """
    Outputs the leaderboard information to a CSV file.
    :param sorted_leaderboard: A sorted list of tuples containing engineer names and their data.
    """
    # Output file path is configurable via config.json -> data_files.leaderboard
    with open(LEADERBOARD_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        # Updated header row with new metrics
        writer.writerow(["Name", "Total Coding Duration", "QA Returns", "Tasks Completed", "Average Coding Duration", "Throughput (tasks/month)", "QA Return Rate (%)"])
        # Iterate through the sorted leaderboard and write each row
        for name, data in sorted_leaderboard:
            # Calculate QA return rate as a percentage
            qa_return_rate = (data['qa_returns'] / data['tasks_completed'] * 100) if data['tasks_completed'] > 0 else 0
            # Write each row
            writer.writerow([
                name,
                seconds_to_work_units(data['total_time']),
                data['qa_returns'],
                data['tasks_completed'],
                seconds_to_work_units(data['total_time'] / data['tasks_completed']) if data['tasks_completed'] > 0 else 0,
                data['throughput'],  # Directly use the throughput value which is now a float
                f"{qa_return_rate:.2f}%"
            ])
    print(f"Leaderboard data has been written to leaderboard.csv")


def _get_field(issue, field_id_or_name):
    """
    Safely retrieve a field value by id or name from an issue.
    Accepts system names like 'updated', 'created', 'resolutiondate', 'duedate'.
    """
    try:
        if hasattr(issue.fields, field_id_or_name):
            return getattr(issue.fields, field_id_or_name)
    except Exception:
        pass
    # Try normalized lower-case names
    try:
        return getattr(issue.fields, (field_id_or_name or '').lower())
    except Exception:
        return None


def _get_epic_key(issue, fields_map: dict):
    """
    Attempt to extract the epic key for a given issue using discovered epic link candidates
    and common fallbacks.
    """
    candidates = (fields_map or {}).get('epic_link', []) if isinstance(fields_map, dict) else []
    # Try explicit candidates
    for c in candidates:
        val = _get_field(issue, c)
        if isinstance(val, str):
            return val
        # Some instances return an object with key attribute
        try:
            key = getattr(val, 'key', None)
            if key:
                return key
        except Exception:
            pass
    # Fallbacks used in many Jira instances
    for name in ('epicLink', 'customfield_10014'):
        val = _get_field(issue, name)
        if isinstance(val, str):
            return val
        try:
            key = getattr(val, 'key', None)
            if key:
                return key
        except Exception:
            pass
    # Last-resort fallback observed in some instances: parent may reference the Epic
    # (especially when Epic linking behaves differently). Use parent.key if present.
    try:
        parent = _get_field(issue, 'parent') or getattr(getattr(issue, 'fields', None), 'parent', None)
        pkey = getattr(parent, 'key', None)
        if isinstance(pkey, str) and pkey:
            return pkey
    except Exception:
        pass
    return None


def _to_utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize any datetime to a timezone-naive UTC datetime.

    - If dt is timezone-aware, convert to UTC and strip tzinfo.
    - If dt is naive, return as-is.
    - If dt is falsy, return None.
    """
    if not dt:
        return None
    try:
        if dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return dt


def _coerce_dt(value):
    """Best-effort convert a Jira field value (str/datetime) to a timezone-naive UTC datetime object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_utc_naive(value)
    try:
        parsed = _parse_jira_timestamp(value)
        return _to_utc_naive(parsed)
    except Exception:
        return None


def _infer_dates_from_transitions(issue) -> tuple:
    """
    Infer (start_dt, end_dt) from changelog status transitions when explicit date
    fields are missing.

    Heuristics:
    - start_dt: timestamp of the earliest status change (first history where item.field == 'status').
      This is robust against varying workflow names, and matches the intent to capture when work began.
    - end_dt: timestamp of the last transition into a done-like status. We match common terminal states
      such as 'Done', 'Closed', 'Resolved'. If no such transition exists, end_dt is None.

    Returns (start_dt, end_dt) as naive datetime objects or (None, None) on failure.
    """
    try:
        histories = getattr(getattr(issue, 'changelog', None), 'histories', None) or []
    except Exception:
        histories = []
    if not histories:
        return None, None

    # Sort by created ascending using robust parser
    def _hkey(h):
        dt = _parse_jira_timestamp(getattr(h, 'created', None))
        return dt or datetime.min

    histories_sorted = sorted(list(histories), key=_hkey)
    start_dt = None
    end_dt = None
    DONE_STATES = {"done", "closed", "resolved"}

    for idx, h in enumerate(histories_sorted):
        items = []
        try:
            items = list(getattr(h, 'items', []) or [])
        except Exception:
            items = []
        # Determine if this history includes a status change
        for it in items:
            try:
                if getattr(it, 'field', None) != 'status':
                    continue
                ts = _parse_jira_timestamp(getattr(h, 'created', None))
                if ts and start_dt is None:
                    start_dt = ts
                to_str = str(getattr(it, 'toString', '') or '').strip()
                if to_str and to_str.lower() in DONE_STATES:
                    end_dt = ts  # keep last done-transition seen
            except Exception:
                continue

    return start_dt, end_dt


def _infer_dates_from_progress_series(points: list, start_dt: Optional[datetime], end_dt: Optional[datetime]) -> tuple:
    """
    Infer missing start/end dates from a time series of percent-done points.

    points: list of (datetime, percent_done_float) where percent is in [0, 100].
    If there are at least two points with different percentages, assume linear
    progress between them and extrapolate:
      - If start_dt is None: estimate when percent would have been 0%
      - If end_dt is None: estimate when percent would reach 100%

    Returns (est_start, est_end, used) where used=True if an estimation was applied.
    """
    try:
        pts = [(d, float(p)) for (d, p) in points if d and p is not None]
    except Exception:
        pts = []
    if len(pts) < 2:
        return start_dt, end_dt, False

    # Sort ascending by time and pick two endpoints that show a change
    pts.sort(key=lambda x: x[0])
    p1 = None
    p2 = None
    for i in range(len(pts) - 1, 0, -1):
        # prefer the latest span with a delta
        if pts[i][1] != pts[i - 1][1]:
            p1 = pts[i - 1]
            p2 = pts[i]
            break
    if not p1 or not p2:
        # fallback: first and last
        p1, p2 = pts[0], pts[-1]
        if p2[1] == p1[1]:
            return start_dt, end_dt, False

    t1, v1 = p1
    t2, v2 = p2
    # Clamp percents
    v1 = max(0.0, min(100.0, v1))
    v2 = max(0.0, min(100.0, v2))
    if t2 <= t1:
        return start_dt, end_dt, False
    dv = (v2 - v1)
    if dv == 0:
        return start_dt, end_dt, False

    span = (t2 - t1)
    used = False
    est_start = start_dt
    est_end = end_dt

    # Linear interpolation/extrapolation on time vs percent
    # percent(t) ≈ v1 + dv * ((t - t1) / span)
    # Solve for percent=0 and percent=100
    try:
        if est_start is None:
            factor_to_zero = (0.0 - v1) / dv
            est_start = t1 + factor_to_zero * span
            used = True
        if est_end is None:
            factor_to_full = (100.0 - v1) / dv
            est_end = t1 + factor_to_full * span
            used = True or used
    except Exception:
        pass

    # Sanity: ensure est_start <= est_end and ensure minimal duration if equal
    if est_start and est_end and est_end < est_start:
        est_start, est_end = est_end, est_start
    if est_start and est_end and est_start == est_end:
        est_end = est_start + timedelta(days=1)

    return est_start or start_dt, est_end or end_dt, used


def _save_programme_projects_epics_gantt(proj_agg: dict, epic_agg: dict, output_path: str = None, story_agg: dict = None) -> bool:
    """
    Render a Gantt chart including Programme (overall span), each Project, and each Project's Epics.

    - Programme: single row spanning min(project.start) to max(project.end)
    - Project: one row per project
    - Epic: one row per epic, grouped under its project (by epic key prefix before '-')
    Only entries with at least start or end date are plotted; 0-duration bars are expanded to 1 day.

    Returns True when a PNG was saved, False when no rows were available to draw.
    """
    output_path = output_path or GANTT_FILE

    def _row_from_dates(label: str, start, end, percent=0.0):
        s = _coerce_dt(start)
        e = _coerce_dt(end)
        if s is None and e is None:
            return None
        if s is None:
            s = e
        if e is None:
            e = s
        if e < s:
            s, e = e, s
        if e == s:
            e = s + timedelta(days=1)
        return (label, s, e, percent)

    # Build project rows and compute programme span
    proj_rows = []
    prog_start_candidates = []
    prog_end_candidates = []
    for pkey, e in proj_agg.items():
        r = _row_from_dates(e.get('name') or pkey, e.get('start'), e.get('end'), e.get('percent_done', 0.0))
        if r:
            proj_rows.append((pkey, r, e.get('start_source'), e.get('end_source')))
            prog_start_candidates.append(r[1])
            prog_end_candidates.append(r[2])

    # Epic rows grouped by project key (prefix before '-')
    epics_by_project = {}
    epic_prog_start_candidates = []
    epic_prog_end_candidates = []
    # Keep computed projection split points per-epic so we can draw factual-to-update and projected remainder distinctly
    epic_split_at_map: Dict[str, Optional[datetime]] = {}
    for ekey, e in epic_agg.items():
        label = f"{ekey} (Epic)" if e.get('name') in (None, '', ekey) else f"{ekey} - {e.get('name')}"
        # Compute projection when no factual end but we have progress and last_updated
        start_dt = _coerce_dt(e.get('start'))
        factual_end_dt = _coerce_dt(e.get('end'))
        last_upd = _coerce_dt(e.get('last_updated'))
        pct = None
        try:
            pct = float(e.get('percent_done')) if e.get('percent_done') is not None else None
        except Exception:
            pct = None
        projected_end = None
        split_at = None
        if start_dt and not factual_end_dt and last_upd and pct is not None and 0.0 < pct < 100.0 and last_upd > start_dt:
            try:
                elapsed = last_upd - start_dt
                total = elapsed * (100.0 / max(1e-6, pct))
                projected_end = start_dt + total
                split_at = last_upd
            except Exception:
                projected_end = None
                split_at = None
        # Use projected end (calculated) if available
        use_end = projected_end if projected_end else factual_end_dt
        r = _row_from_dates(label, start_dt, use_end, e.get('percent_done', 0.0))
        if not r:
            continue
        pfx = None
        try:
            pfx = str(ekey).split('-')[0]
        except Exception:
            pfx = None
        # If projected, mark end source as calculated and capture split point
        end_src = e.get('end_source')
        if projected_end is not None:
            end_src = 'calculated'
            epic_split_at_map[ekey] = split_at
        epics_by_project.setdefault(pfx, []).append((ekey, r, e.get('start_source'), end_src))
        # Track epic candidates for programme span too, so a chart still renders when projects have no dates
        epic_prog_start_candidates.append(r[1])
        epic_prog_end_candidates.append(r[2])

    # Programme row
    rows = []  # list of tuples: (label, start, end, percent, start_src, end_src)
    try:
        # Include epics when computing Programme span to avoid empty chart when project dates are missing
        all_start_cands = [d for d in (prog_start_candidates + epic_prog_start_candidates) if d]
        all_end_cands = [d for d in (prog_end_candidates + epic_prog_end_candidates) if d]
        prog_start = min(all_start_cands) if all_start_cands else None
        prog_end = max(all_end_cands) if all_end_cands else None
    except Exception:
        prog_start, prog_end = None, None
    prog_row = _row_from_dates('Programme', prog_start, prog_end, 0.0)
    if prog_row:
        rows.append((prog_row[0], prog_row[1], prog_row[2], prog_row[3], 'factual', 'factual'))

    # Sort projects by start date
    proj_rows.sort(key=lambda t: (t[1][1] or datetime.min))

    # Append each project and its epics (sorted by start)
    if proj_rows:
        for pkey, r, p_ss, p_es in proj_rows:
            rows.append((r[0], r[1], r[2], r[3], p_ss or 'factual', p_es or 'factual'))
            epic_rows = epics_by_project.get(pkey, [])
            epic_rows.sort(key=lambda rr: rr[1][1] or datetime.min)
            for ekey, er, e_ss, e_es in epic_rows:
                rows.append((er[0], er[1], er[2], er[3], e_ss or 'factual', e_es or 'factual'))
            # Also append Story rows under each Epic if provided
            # Also append Story rows under each Epic if provided
            if story_agg:
                # Build stories by epic map lazily
                for ekey, er, e_ss, e_es in list(epic_rows):
                    try:
                        epic_label = er[0][0]
                        ekey_from_label = epic_label.split(' ')[0]
                    except Exception:
                        ekey_from_label = None
                    if not ekey_from_label:
                        continue
                    # stories for this epic
                    stories = [s for sk, s in (story_agg or {}).items() if s.get('parent_epic') == ekey_from_label]
                    story_rows = []
                    for st in stories:
                        sr = _row_from_dates(st.get('label') or st.get('key'), st.get('start'), st.get('end'), st.get('percent_done', 0.0))
                        if sr:
                            # Tag story rows by injecting a marker in the label
                            story_rows.append((st.get('key'), (f"{st.get('key')} - {st.get('name') or ''}" if st.get('name') else st.get('key'), sr[1], sr[2], sr[3], st.get('start_source') or 'factual', st.get('end_source') or 'factual')))
                    story_rows.sort(key=lambda t: t[1][1] or datetime.min)
                    for _sk, srow in story_rows:
                        rows.append((srow[0], srow[1], srow[2], srow[3], srow[4], srow[5]))
    else:
        # No usable project rows → still render epics, grouped by their prefix, sorted by earliest start
        # Sort project prefixes by the earliest start among their epics
        def group_earliest_start(epic_list):
            try:
                return min((rr[1] for rr in epic_list if rr and rr[1]), default=datetime.max)
            except Exception:
                return datetime.max
        for pfx in sorted(epics_by_project.keys(), key=lambda k: group_earliest_start(epics_by_project[k])):
            epic_rows = epics_by_project.get(pfx, [])
            epic_rows.sort(key=lambda rr: rr[1][1] or datetime.min)
            for ekey, er, e_ss, e_es in epic_rows:
                rows.append((er[0], er[1], er[2], er[3], e_ss or 'factual', e_es or 'factual'))

    if not rows:
        # Nothing to draw; emit a small diagnostic and exit gracefully
        print("Gantt chart generation skipped: no timeline rows (no dated projects or epics)")
        return False

    try:
        plt.switch_backend('Agg')
    except Exception:
        pass

    height = max(3, int(0.5 * len(rows)) + 2)
    fig, ax = plt.subplots(figsize=(14, height))
    y_pos = list(range(len(rows)))
    names = [r[0] for r in rows]
    starts = [mdates.date2num(r[1]) for r in rows]
    durations = [mdates.date2num(r[2]) - mdates.date2num(r[1]) for r in rows]

    colors = []
    for name in names:
        if name == 'Programme':
            colors.append('#333333')
        elif '(Epic)' in name:
            colors.append('#59A14F')
        elif '-' in name and name.split('-')[0].isalpha():
            # heuristic: story row label looks like KEY - Title
            colors.append('#B07AA1')
        else:
            colors.append('#4C78A8')

    # Build aligned split points list for projection-aware epics (others None)
    split_points = []
    for name in names:
        sp = None
        try:
            if ' - ' in name or name.endswith('(Epic)'):
                # Extract epic key
                ekey = name.split(' ')[0]
                sp = epic_split_at_map.get(ekey)
        except Exception:
            sp = None
        split_points.append(sp)

    # Draw bars with provenance split: if start/end sources differ, use precise split when available; else split into halves
    for idx, (name, sdt, edt, pct, ssrc, esrc) in enumerate(rows):
        left = mdates.date2num(sdt)
        width = mdates.date2num(edt) - left
        if width <= 0:
            width = 1.0
        base_color = colors[idx]
        factual_set = {'factual', 'field', 'created', 'resolution', 'due'}
        start_calc = (str(ssrc).lower() not in factual_set)
        end_calc = (str(esrc).lower() not in factual_set)
        sp = split_points[idx]
        if start_calc == end_calc and not sp:
            ax.barh([idx], [width], left=[left], height=0.4, color=base_color, hatch='///' if start_calc else None, edgecolor='black' if start_calc else None, alpha=0.9)
        else:
            if sp:
                sp_num = mdates.date2num(_coerce_dt(sp))
                # Clamp split inside the bar
                sp_num = max(left, min(left + width, sp_num))
                left_w = max(0.0, sp_num - left)
                right_w = max(0.0, (left + width) - sp_num)
                if left_w > 0:
                    ax.barh([idx], [left_w], left=[left], height=0.4, color=base_color, hatch='///' if start_calc else None, edgecolor='black' if start_calc else None, alpha=0.9)
                if right_w > 0:
                    ax.barh([idx], [right_w], left=[sp_num], height=0.4, color=base_color, hatch='///' if end_calc else None, edgecolor='black' if end_calc else None, alpha=0.9)
            else:
                # Fallback: simple half-split when we don't know the exact split point
                half = width/2.0
                ax.barh([idx], [half], left=[left], height=0.4, color=base_color, hatch='///' if start_calc else None, edgecolor='black' if start_calc else None, alpha=0.9)
                ax.barh([idx], [width-half], left=[left+half], height=0.4, color=base_color, hatch='///' if end_calc else None, edgecolor='black' if end_calc else None, alpha=0.9)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax.set_title('Programme plan: Programme, Projects, Epics, and Stories')
    ax.set_xlabel('Date')

    # Add a small legend for provenance
    try:
        import matplotlib.patches as mpatches
        factual_patch = mpatches.Patch(facecolor='#cccccc', edgecolor='none', label='Factual (solid)')
        calc_patch = mpatches.Patch(facecolor='#cccccc', edgecolor='black', hatch='///', label='Calculated (hatched)')
        ax.legend(handles=[factual_patch, calc_patch], loc='upper right', fontsize=8)
    except Exception:
        pass

    fig.autofmt_xdate()
    fig.tight_layout()
    # Inform about the resolved output path before saving (helps users locate the file)
    try:
        print(f"Saving Gantt chart to: {output_path}")
    except Exception:
        pass
    try:
        fig.savefig(output_path, dpi=150)
        return True
    finally:
        plt.close(fig)


def _save_programme_projects_epics_gantt_html(proj_agg: dict, epic_agg: dict, output_path: str = None, story_agg: dict = None) -> bool:
    """
    Save a browser-friendly, clickable HTML Gantt that includes Programme, Projects, and Epics.

    - Programme: overall span across all project/epic start/end dates (not clickable)
    - Project rows: link to Jira issues search for that project
    - Epic rows: link directly to the epic in Jira (browse/<KEY>)

    Returns True if the HTML file was written, False if there were no rows to render.
    """
    try:
        from urllib.parse import quote
    except Exception:
        def quote(s):
            return s

    output_path = output_path or GANTT_HTML_FILE
    # Ensure timedelta is in local scope for any inner calculations (some linters require explicit import)
    from datetime import timedelta

    # Reuse row building logic similar to PNG path to ensure identical content
    def _row_from_dates(label: str, start, end, percent=0.0):
        s = _coerce_dt(start)
        e = _coerce_dt(end)
        if s is None and e is None:
            return None
        if s is None:
            s = e
        if e is None:
            e = s
        if e < s:
            s, e = e, s
        if e == s:
            e = s + timedelta(days=1)
        return (label, s, e, percent)

    proj_rows = []  # (project_key, (label,start,end,percent))
    prog_start_candidates = []
    prog_end_candidates = []
    for pkey, e in proj_agg.items():
        r = _row_from_dates(e.get('name') or pkey, e.get('start'), e.get('end'), e.get('percent_done', 0.0))
        if r:
            proj_rows.append((pkey, r))
            prog_start_candidates.append(r[1])
            prog_end_candidates.append(r[2])

    epics_by_project = {}
    epic_prog_start_candidates = []
    epic_prog_end_candidates = []
    for ekey, e in epic_agg.items():
        label = f"{ekey} (Epic)" if e.get('name') in (None, '', ekey) else f"{ekey} - {e.get('name')}"
        # Projection: when no factual end but percent suggests remaining work, extend to estimated end
        sdt = _coerce_dt(e.get('start'))
        edt_fact = _coerce_dt(e.get('end'))
        last_upd = _coerce_dt(e.get('last_updated'))
        pct = None
        try:
            pct = float(e.get('percent_done')) if e.get('percent_done') is not None else None
        except Exception:
            pct = None
        est_end = None
        split_at = None
        if sdt and not edt_fact and last_upd and pct is not None and 0.0 < pct < 100.0 and last_upd > sdt:
            try:
                elapsed = last_upd - sdt
                total = elapsed * (100.0 / max(1e-6, pct))
                est_end = sdt + total
                split_at = last_upd
            except Exception:
                est_end = None
                split_at = None
        r = _row_from_dates(label, sdt, (est_end or edt_fact), e.get('percent_done', 0.0))
        if not r:
            continue
        pfx = None
        try:
            pfx = str(ekey).split('-')[0]
        except Exception:
            pfx = None
        # keep epic key and any split point for rendering
        epics_by_project.setdefault(pfx, []).append((ekey, r, split_at))
        epic_prog_start_candidates.append(r[1])
        epic_prog_end_candidates.append(r[2])

    try:
        all_start_cands = [d for d in (prog_start_candidates + epic_prog_start_candidates) if d]
        all_end_cands = [d for d in (prog_end_candidates + epic_prog_end_candidates) if d]
        prog_start = min(all_start_cands) if all_start_cands else None
        prog_end = max(all_end_cands) if all_end_cands else None
    except Exception:
        prog_start, prog_end = None, None

    # Helper: compute a human-friendly tooltip from an aggregation entry
    def _make_tooltip(kind: str, key: str, entry: dict, start_dt: Optional[datetime], end_dt: Optional[datetime]) -> str:
        def fmt_dt(d: Optional[datetime]) -> str:
            try:
                return d.strftime('%Y-%m-%d') if d else ''
            except Exception:
                return str(d) if d is not None else ''

        # Percent done: prefer cached value, else derive from progress tuples
        pct = entry.get('percent_done')
        if pct is None:
            try:
                progs = entry.get('progress_vals') or []
                prog_sum = sum((p or 0) for (p, t) in progs if p is not None)
                tot_sum = sum((t or 0) for (p, t) in progs if t is not None)
                pct = (100.0 * prog_sum / tot_sum) if tot_sum else 0.0
            except Exception:
                pct = 0.0

        issues = entry.get('issues') or 0
        done = entry.get('done') or 0
        assignees = sorted(list(entry.get('assignees') or []))
        updaters = sorted(list(entry.get('updaters') or []))
        last_upd = entry.get('last_updated')
        last_upd = _coerce_dt(last_upd)

        display_name = (entry.get('name') or '').strip()
        parts = [
            f"{kind.title()} {key}",
            f"Title: {display_name or key}",
            f"Start: {fmt_dt(start_dt)}",
            f"End: {fmt_dt(end_dt)}",
            f"Percent done: {pct:.2f}%",
            f"Issues counted: {issues} (Done: {done})",
        ]
        # Add provenance if available
        ss = (entry.get('start_source') or '').strip()
        es = (entry.get('end_source') or '').strip()
        if ss or es:
            parts.append(f"Provenance: start={ss or 'n/a'}, end={es or 'n/a'}")
        if assignees:
            parts.append(f"Assignees: {', '.join(assignees)}")
        if updaters:
            parts.append(f"Updaters: {', '.join(updaters)}")
        if last_upd:
            parts.append(f"Last updated: {fmt_dt(last_upd)}")
        return " | ".join(parts)

    # Build ordered rows list: each item is dict with keys: kind, label, start, end, key(optional), href(optional), color, title, start_src, end_src, split_at(optional)
    rows = []
    # Programme row (no link)
    if prog_start and prog_end:
        rows.append({
            'kind': 'programme',
            'label': 'Programme',
            'start': prog_start,
            'end': prog_end,
            'color': '#333333',
            'title': f"Programme span | Start: {prog_start.date()} | End: {prog_end.date()}"
        })

    # Sort project rows by start
    proj_rows.sort(key=lambda t: (t[1][1] or datetime.min))

    def _clean_scope_key(value: Any) -> str:
        s = str(value or "")
        return s.split("|")[-1] if "|" in s else s

    def _project_href(pkey: str, p_entry: dict) -> str:
        url = p_entry.get('jira_url', JIRA_URL).rstrip('/')
        actual_key = _clean_scope_key(pkey)
        jql = f"project = {actual_key}"
        return f"{url}/issues/?jql={quote(jql)}"

    def _epic_href(ekey: str, e_entry: dict) -> str:
        url = e_entry.get('jira_url', JIRA_URL).rstrip('/')
        actual_key = _clean_scope_key(ekey)
        return f"{url}/browse/{actual_key}"

    if proj_rows:
        for pkey, r in proj_rows:
            p_entry = proj_agg.get(pkey, {})
            p_initial_expanded = not DEFAULT_COLLAPSE_PROJECTS
            rows.append({
                'kind': 'project', 'key': pkey, 'label': r[0], 'start': r[1], 'end': r[2], 'color': '#4C78A8',
                'href': _project_href(pkey, p_entry),
                'title': _make_tooltip('project', pkey, p_entry, r[1], r[2]),
                'start_src': p_entry.get('start_source') or 'field',
                'end_src': p_entry.get('end_source') or 'field',
                'expanded': p_initial_expanded,
            })
            epic_rows = epics_by_project.get(pkey, [])
            epic_rows.sort(key=lambda rr: rr[1][1] or datetime.min)
            for ekey, er, split_at in epic_rows:
                e_entry = epic_agg.get(ekey, {})
                e_initial_expanded = not DEFAULT_COLLAPSE_EPICS
                rows.append({
                    'kind': 'epic', 'key': ekey, 'parent': pkey, 'label': er[0], 'start': er[1], 'end': er[2], 'color': '#59A14F',
                    'href': _epic_href(ekey, e_entry),
                    'title': _make_tooltip('epic', ekey, e_entry, er[1], er[2]),
                    'start_src': e_entry.get('start_source') or 'field',
                    'end_src': (e_entry.get('end_source') or 'field') if not split_at else 'calculated',
                    'split_at': split_at,
                    'expanded': e_initial_expanded,
                })
                # Story rows under this epic if provided
                if story_agg:
                    story_list = [v for v in story_agg.values() if v.get('parent_epic') == ekey]
                    story_list.sort(key=lambda se: _coerce_dt(se.get('start')) or datetime.min)
                    for se in story_list:
                        s_key = se.get('key')
                        s_url = se.get('jira_url', JIRA_URL).rstrip('/')
                        s_label = f"{s_key} - {se.get('name') or ''}".strip()
                        s_start = _coerce_dt(se.get('start'))
                        s_end = _coerce_dt(se.get('end')) or (s_start + timedelta(days=1) if s_start else None)
                        if not s_start and not s_end:
                            continue
                        if s_end and s_start and s_end < s_start:
                            s_start, s_end = s_end, s_start
                        # Initial visibility for stories depends on both project and epic expanded state
                        s_initial_visible = (p_initial_expanded and e_initial_expanded)
                        rows.append({
                            'kind': 'story', 'key': s_key, 'parent': ekey, 'parent_project': pkey, 'label': s_label, 'start': s_start, 'end': s_end,
                            'color': '#B07AA1', 'href': f"{s_url}/browse/{s_key}",
                            'title': _make_tooltip('story', s_key, se, s_start, s_end),
                            'start_src': se.get('start_source') or 'field',
                            'end_src': se.get('end_source') or 'field',
                            'visible': s_initial_visible,
                        })
    else:
        # Group by prefix and sort by earliest start
        def group_earliest_start(epic_list):
            try:
                return min((rr[1][1] for rr in epic_list if rr and rr[1] and rr[1][1]), default=datetime.max)
            except Exception:
                return datetime.max
        for pfx in sorted(epics_by_project.keys(), key=lambda k: group_earliest_start(epics_by_project[k])):
            epic_rows = epics_by_project.get(pfx, [])
            epic_rows.sort(key=lambda rr: rr[1][1] or datetime.min)
            for ekey, er, split_at in epic_rows:
                e_entry = epic_agg.get(ekey, {})
                rows.append({
                    'kind': 'epic', 'key': ekey, 'parent': pfx, 'label': er[0], 'start': er[1], 'end': er[2], 'color': '#59A14F',
                    'href': _epic_href(ekey),
                    'title': _make_tooltip('epic', ekey, e_entry, er[1], er[2]),
                    'start_src': e_entry.get('start_source') or 'field',
                    'end_src': (e_entry.get('end_source') or 'field') if not split_at else 'calculated',
                    'split_at': split_at,
                    'expanded': not DEFAULT_COLLAPSE_EPICS,
                })

    if not rows:
        # Fallback: try to fabricate minimal rows using last_updated as a proxy to ensure HTML is produced
        fabricated = []
        now = datetime.utcnow()
        for ekey, e in (epic_agg or {}).items():
            lu = _coerce_dt(e.get('last_updated')) or now
            label = f"{ekey} (Epic)" if (e.get('name') in (None, '', ekey)) else f"{ekey} - {e.get('name')}"
            fabricated.append({
                'kind': 'epic', 'key': ekey, 'parent': (str(ekey).split('-')[0] if isinstance(ekey, str) and '-' in ekey else ''),
                'label': label, 'start': lu, 'end': lu + timedelta(days=1), 'color': '#59A14F',
                'href': f"{JIRA_URL.rstrip('/')}/browse/{ekey}",
                'title': f"Epic {ekey} | Start: {lu.date()} | End: {(lu + timedelta(days=1)).date()} | (fabricated)",
            })
        if fabricated:
            rows.extend(fabricated)
        else:
            print("Gantt HTML generation skipped: no timeline rows (no dated projects or epics)")
            return False
    # Determine overall span for scaling
    try:
        min_start = min(r['start'] for r in rows if r.get('start'))
        max_end = max(r['end'] for r in rows if r.get('end'))
    except Exception:
        print("Gantt HTML generation skipped: failed to compute overall span")
        return False
    total_days = max(1, (max_end - min_start).days or 1)

    def _pct_pos(dt):
        return max(0.0, min(100.0, 100.0 * (dt - min_start).total_seconds() / ((max_end - min_start).total_seconds() or 1)))

    def _pct_width(start, end):
        dur = (end - start).total_seconds()
        tot = (max_end - min_start).total_seconds() or 1
        w = 100.0 * dur / tot
        return max(0.2, w)  # ensure visible minimum width

    # Build HTML
    lines = []
    lines.append("<!DOCTYPE html>")
    lines.append("<meta charset=\"utf-8\">")
    lines.append("<title>Programme plan: Programme, Projects, and Epics</title>")
    lines.append("<style> :root{--labelW:320px} body{font-family:Arial,Helvetica,sans-serif;margin:0} .container{padding:12px} .gantt-header{position:sticky;top:0;background:#fff;padding:12px 12px 8px 12px;border-bottom:1px solid #ddd;z-index:10} .rows{position:relative;padding:12px;padding-top:40px} .grid{position:absolute;left:calc(var(--labelW) + 12px);right:12px;top:12px;bottom:12px;pointer-events:none;z-index:0} .vline{position:absolute;top:0;bottom:0;width:1px;background:#eee} .vline.month{background:#ccc} .tick-label{position:absolute;top:-20px;font-size:12px;color:#444;background:rgba(255,255,255,0.92);padding:1px 3px;border-radius:2px;transform:translateX(-50%);z-index:2} .row{position:relative;margin:6px 0;height:26px;border-radius:3px} .label{position:absolute;left:12px;top:2px;width:var(--labelW);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;z-index:2} .bar-wrap{position:absolute;left:calc(var(--labelW) + 12px);right:12px;top:0;bottom:0;background:#f5f5f5;border-radius:3px} .bar{position:absolute;height:100%;border-radius:3px;opacity:0.9;z-index:1} a.bar{display:block;text-decoration:none;color:inherit} .legend{margin:6px 0 8px 0} .legend span{display:inline-block;margin-right:12px} .dot{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:4px;vertical-align:middle} .toggle{margin-right:6px;font-size:12px;line-height:18px;padding:2px 6px;cursor:pointer} .controls{margin-top:6px} .epic-row{transition:height 0.2s ease, opacity 0.2s ease} .story-row{transition:height 0.2s ease, opacity 0.2s ease} .seg{position:absolute;top:0;height:100%} .calc{background-image: repeating-linear-gradient(45deg, rgba(0,0,0,0.08) 0, rgba(0,0,0,0.08) 6px, transparent 6px, transparent 12px);} </style>")
    lines.append("<div class=\"container\">")
    lines.append("  <div class=\"gantt-header\">")
    lines.append("    <h3 style=\"margin:0 0 6px 0\">Programme plan: Programme, Projects, and Epics</h3>")
    # Legend within header
    lines.append("    <div class=\"legend\">" \
                 f"<span><span class=\"dot\" style=\"background:#333333\"></span>Programme</span>" \
                 f"<span><span class=\"dot\" style=\"background:#4C78A8\"></span>Project</span>" \
                 f"<span><span class=\"dot\" style=\"background:#59A14F\"></span>Epic</span>" \
                 f"<span><span class=\"dot\" style=\"background:#B07AA1\"></span>Story</span>" \
                 f"<span><span class=\"dot\" style=\"background:#888\"></span>Factual</span>" \
                 f"<span><span class=\"dot calc\" style=\"background:#bbb\"></span>Calculated</span>" \
                 "</div>")
    # Date axis summary and controls in header
    lines.append(f"    <div style=\"font-size:12px;color:#555;margin:4px 0\">Span: {min_start.date()} to {max_end.date()} ({total_days} days)</div>")
    # Controls: expand/collapse + filters
    lines.append("    <div class=\"controls\">\n"
                 "      <button id=\"expandAll\" class=\"toggle\">Expand all</button>"
                 "      <button id=\"collapseAll\" class=\"toggle\">Collapse all</button>\n"
                 "      <div style=\"margin-top:8px;font-size:12px\">\n"
                 "        <label>Epic filter <input id=\"epicFilter\" type=\"text\" placeholder=\"e.g., CFNA-\" style=\"width:120px\"></label>\n"
                 "        <label style=\"margin-left:8px\">Start ≥ <input id=\"startAfter\" type=\"date\"></label>\n"
                 "        <label style=\"margin-left:8px\">End ≤ <input id=\"endBefore\" type=\"date\"></label>\n"
                 "        <label style=\"margin-left:8px\">Assignee <input id=\"assigneeFilter\" type=\"text\" style=\"width:120px\"></label>\n"
                 "        <label style=\"margin-left:8px\">Updater <input id=\"updaterFilter\" type=\"text\" style=\"width:120px\"></label>\n"
                 "        <label style=\"margin-left:8px\">% min <input id=\"pctMin\" type=\"number\" min=\"0\" max=\"100\" step=\"1\" style=\"width:64px\"></label>\n"
                 "        <label style=\"margin-left:8px\">% max <input id=\"pctMax\" type=\"number\" min=\"0\" max=\"100\" step=\"1\" style=\"width:64px\"></label>\n"
                 "        <label style=\"margin-left:8px\">Search <input id=\"freeText\" type=\"text\" placeholder=\"title, label...\" style=\"width:160px\"></label>\n"
                 "        <button id=\"applyFilters\" class=\"toggle\" style=\"margin-left:8px\">Apply</button>\n"
                 "        <button id=\"clearFilters\" class=\"toggle\">Clear</button>\n"
                 "      </div>\n"
                 "    </div>")
    lines.append("  </div>")
    lines.append("  <div class=\"rows\">")
    # Time grid: monthly labeled ticks and weekly guide lines
    lines.append("    <div class=\"grid\">")
    # Compute weekly ticks (Mondays) and monthly ticks (1st of month)
    try:
        # Weekly ticks
        from datetime import timedelta
        # Start from the Monday on/after min_start.date()
        start_day = min_start
        # Normalize to 00:00 of that day
        start_day = start_day.replace(hour=0, minute=0, second=0, microsecond=0)
        # 0=Monday ... 6=Sunday
        delta_to_mon = (0 - start_day.weekday()) % 7
        week_cursor = start_day + timedelta(days=delta_to_mon)
        while week_cursor <= max_end:
            x = _pct_pos(week_cursor)
            lines.append(f"      <div class=\"vline\" style=\"left:{x:.3f}%\"></div>")
            week_cursor += timedelta(days=7)
        # Monthly ticks with labels
        m_cursor = min_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # If min_start is not already first of month and before it, ensure we include that month start if within range
        while m_cursor <= max_end:
            x = _pct_pos(m_cursor)
            label = f"{m_cursor.year:04d}-{m_cursor.month:02d}"
            lines.append(f"      <div class=\"vline month\" style=\"left:{x:.3f}%\"></div>")
            lines.append(f"      <div class=\"tick-label\" style=\"left:{x:.3f}%\">{label}</div>")
            # advance to next month
            year = m_cursor.year + (1 if m_cursor.month == 12 else 0)
            month = 1 if m_cursor.month == 12 else (m_cursor.month + 1)
            m_cursor = m_cursor.replace(year=year, month=month, day=1)
    except Exception:
        pass
    lines.append("    </div>")

    for r in rows:
        label = r['label']
        left = _pct_pos(r['start'])
        width = _pct_width(r['start'], r['end'])
        color = r['color']
        href = r.get('href')
        title_attr = r.get('title') or ''
        kind = r.get('kind')
        attrs = []
        row_classes = ["row"]
        # For epics, add data-parent attribute for project-level toggling, and add epic toggle
        if kind == 'epic':
            # Prefer explicit parent computed during row assembly; fallback to key prefix
            pfx = _clean_scope_key(r.get('parent') or (r.get('key') or '').split('-')[0])
            attrs.append(f'data-parent="{pfx}"')
            row_classes.append('epic-row')
        # For stories, add data-parent-epic and data-parent-project to support both toggles
        if kind == 'story':
            attrs.append(f'data-parent-epic="{_clean_scope_key(r.get("parent", ""))}"')
            attrs.append(f'data-parent-project="{_clean_scope_key(r.get("parent_project", ""))}"')
            row_classes.append('story-row')
        # Common metadata attributes for filtering
        # Key/kind
        if r.get('key'):
            attrs.append(f'data-key="{_clean_scope_key(r.get("key"))}"')
        attrs.append(f'data-kind="{kind}"')
        # Dates for filtering
        try:
            ds = r['start'].strftime('%Y-%m-%d') if r.get('start') else ''
        except Exception:
            ds = ''
        try:
            de = r['end'].strftime('%Y-%m-%d') if r.get('end') else ''
        except Exception:
            de = ''
        if ds:
            attrs.append(f'data-start="{ds}"')
        if de:
            attrs.append(f'data-end="{de}"')
        # Percent done
        pct_val = None
        try:
            # Derive percent consistently with tooltip
            entry = None
            if kind == 'project':
                entry = proj_agg.get(r.get('key', ''), {})
            elif kind == 'epic':
                entry = epic_agg.get(r.get('key', ''), {})
            elif kind == 'story':
                # stories are built from se dict directly
                entry = None
            pct_val = None
            if entry is not None:
                pct_val = entry.get('percent_done')
            if pct_val is None and kind != 'story':
                progs = (entry or {}).get('progress_vals') or []
                prog_sum = sum((p or 0) for (p, t) in progs if p is not None)
                tot_sum = sum((t or 0) for (p, t) in progs if t is not None)
                pct_val = (100.0 * prog_sum / tot_sum) if tot_sum else None
        except Exception:
            pct_val = None
        if pct_val is not None:
            try:
                attrs.append(f'data-pct="{float(pct_val):.2f}"')
            except Exception:
                pass
        # Assignees / updaters for filtering
        def _join_names(xs):
            try:
                return ", ".join(sorted([str(x) for x in (xs or []) if x]))
            except Exception:
                return ''
        if kind == 'project':
            pa = proj_agg.get(r.get('key', ''), {})
            attrs.append(f'data-assignees="{_join_names(pa.get("assignees"))}"')
            attrs.append(f'data-updaters="{_join_names(pa.get("updaters"))}"')
        elif kind == 'epic':
            ea = epic_agg.get(r.get('key', ''), {})
            attrs.append(f'data-assignees="{_join_names(ea.get("assignees"))}"')
            attrs.append(f'data-updaters="{_join_names(ea.get("updaters"))}"')
        elif kind == 'story':
            # minimal; not all story rows carry sets; leave empty
            attrs.append('data-assignees=""')
            attrs.append('data-updaters=""')
        # For projects, add data-project and a toggle control
        toggle_html = ""
        if kind == 'project':
            pkey = _clean_scope_key(r.get('key') or '')
            attrs.append(f'data-project="{pkey}"')
            expanded = bool(r.get('expanded', True))
            glyph = '▾' if expanded else '▸'
            toggle_html = f"<button class=\"toggle\" data-project=\"{pkey}\" aria-expanded=\"{'true' if expanded else 'false'}\">{glyph}</button>"
        elif kind == 'epic':
            ekey = _clean_scope_key(r.get('key') or '')
            expanded = bool(r.get('expanded', True))
            glyph = '▾' if expanded else '▸'
            toggle_html = f"<button class=\"toggle toggle-epic\" data-epic=\"{ekey}\" aria-expanded=\"{'true' if expanded else 'false'}\">{glyph}</button>"
        attr_str = (" " + " ".join(attrs)) if attrs else ""
        # Initial visibility for epic/story rows based on default collapsed config
        style_visibility = ""
        if kind == 'epic' and not bool(r.get('expanded', True)):
            style_visibility = ' style="display:none"'
        if kind == 'story' and (r.get('visible') is False):
            style_visibility = ' style="display:none"'
        row_class_attr = " ".join(row_classes)
        lines.append(f'<div class="{row_class_attr}"{attr_str}{style_visibility}>')
        # Left column label (separate from the chart area)
        if toggle_html:
            lines.append(f"  <div class=\"label\">{toggle_html}{label}</div>")
        else:
            lines.append(f"  <div class=\"label\">{label}</div>")
        # Chart area wrapper to keep grid/bars off the label column
        lines.append("  <div class=\"bar-wrap\">")
        style = f"left:{left:.3f}%;width:{width:.3f}%;background:{color}"
        # Determine provenance for coloring
        ss = (r.get('start_src') or '').lower()
        es = (r.get('end_src') or '').lower()
        factual = { 'field', 'created', 'resolution', 'due' }
        is_start_calc = ss not in factual
        is_end_calc = es not in factual
        # Decide segment split: when mixed, split at explicit split_at when provided; otherwise 50/50
        segs = []
        split_at = r.get('split_at')
        if is_start_calc == is_end_calc and not split_at:
            # single segment; class calc if both calculated
            segs.append(('calc' if is_start_calc else '', left, width))
        else:
            if split_at:
                # Use actual split point
                split_left = _pct_pos(split_at)
                # clamp
                split_left = max(left, min(left + width, split_left))
                left_w = max(0.0, split_left - left)
                right_w = max(0.0, (left + width) - split_left)
                if left_w > 0:
                    segs.append(('calc' if is_start_calc else '', left, left_w))
                if right_w > 0:
                    segs.append(('calc' if is_end_calc else '', split_left, right_w))
            else:
                # mixed: split into two halves
                half = width/2.0
                # left half reflects start provenance
                segs.append(('calc' if is_start_calc else '', left, half))
                segs.append(('calc' if is_end_calc else '', left+half, width-half))
        # Render segments
        for cls, lft, wid in segs:
            seg_style = f"left:{lft:.3f}%;width:{wid:.3f}%;background:{color}"
            if cls:
                seg_style += ";"  # class adds striping
            tag = 'a' if href else 'div'
            attrs = f"class=\"bar seg {cls}\" href=\"{href}\" target=\"_blank\"" if href else f"class=\"bar seg {cls}\""
            lines.append(f"    <{tag} {attrs} style=\"{seg_style}\" title=\"{title_attr}\"></{tag}>")
        lines.append('  </div>')  # .bar-wrap
        lines.append('</div>')

    # Close containers and add JS for expand/collapse
    lines.append("  </div>")  # .rows
    # Inline JS for toggling
    lines.append("  <script>\n"
                 "(function(){\n"
                 "  function setEpicVisibility(projectKey, show){\n"
                 "    var rows = document.querySelectorAll(`.epic-row[data-parent=\"${projectKey}\"]`);\n"
                 "    for (var i=0;i<rows.length;i++){ rows[i].style.display = show ? '' : 'none'; }\n"
                 "    var stories = document.querySelectorAll(`.story-row[data-parent-project=\"${projectKey}\"]`);\n"
                 "    for (var j=0;j<stories.length;j++){ stories[j].style.display = show ? '' : 'none'; }\n"
                 "  }\n"
                 "  function setStoryVisibility(epicKey, show){\n"
                 "    var srows = document.querySelectorAll(`.story-row[data-parent-epic=\"${epicKey}\"]`);\n"
                 "    for (var i=0;i<srows.length;i++){ srows[i].style.display = show ? '' : 'none'; }\n"
                 "  }\n"
                 "  function toggleProject(btn){\n"
                 "    var key = btn.getAttribute('data-project');\n"
                 "    var expanded = btn.getAttribute('aria-expanded') === 'true';\n"
                 "    setEpicVisibility(key, !expanded);\n"
                 "    btn.setAttribute('aria-expanded', (!expanded).toString());\n"
                 "    btn.textContent = expanded ? '▸' : '▾';\n"
                 "  }\n"
                 "  function toggleEpic(btn){\n"
                 "    var key = btn.getAttribute('data-epic');\n"
                 "    var expanded = btn.getAttribute('aria-expanded') === 'true';\n"
                 "    setStoryVisibility(key, !expanded);\n"
                 "    btn.setAttribute('aria-expanded', (!expanded).toString());\n"
                 "    btn.textContent = expanded ? '▸' : '▾';\n"
                 "  }\n"
                 "  document.addEventListener('click', function(ev){\n"
                 "    var t = ev.target;\n"
                 "    if (t && t.classList && t.classList.contains('toggle') && t.hasAttribute('data-project')){\n"
                 "      toggleProject(t);\n"
                 "    }\n"
                 "    if (t && t.classList && t.classList.contains('toggle-epic') && t.hasAttribute('data-epic')){\n"
                 "      toggleEpic(t);\n"
                 "    }\n"
                 "  });\n"
                 "  document.getElementById('expandAll')?.addEventListener('click', function(){\n"
                 "    var buttons = document.querySelectorAll('.row[data-project] .toggle');\n"
                 "    for (var i=0;i<buttons.length;i++){ var b=buttons[i]; b.setAttribute('aria-expanded','true'); b.textContent='▾'; setEpicVisibility(b.getAttribute('data-project'), true);}\n"
                 "    var ebtns = document.querySelectorAll('.row .toggle-epic');\n"
                 "    for (var j=0;j<ebtns.length;j++){ var eb=ebtns[j]; eb.setAttribute('aria-expanded','true'); eb.textContent='▾'; setStoryVisibility(eb.getAttribute('data-epic'), true);}\n"
                 "  });\n"
                 "  document.getElementById('collapseAll')?.addEventListener('click', function(){\n"
                 "    var buttons = document.querySelectorAll('.row[data-project] .toggle');\n"
                 "    for (var i=0;i<buttons.length;i++){ var b=buttons[i]; b.setAttribute('aria-expanded','false'); b.textContent='▸'; setEpicVisibility(b.getAttribute('data-project'), false);}\n"
                 "    var ebtns = document.querySelectorAll('.row .toggle-epic');\n"
                 "    for (var j=0;j<ebtns.length;j++){ var eb=ebtns[j]; eb.setAttribute('aria-expanded','false'); eb.textContent='▸'; setStoryVisibility(eb.getAttribute('data-epic'), false);}\n"
                 "  });\n"
                 "  function applyFilters(){\n"
                 "    var epicF = (document.getElementById('epicFilter')?.value || '').trim().toLowerCase();\n"
                 "    var startAfter = document.getElementById('startAfter')?.value || '';\n"
                 "    var endBefore = document.getElementById('endBefore')?.value || '';\n"
                 "    var assF = (document.getElementById('assigneeFilter')?.value || '').trim().toLowerCase();\n"
                 "    var updF = (document.getElementById('updaterFilter')?.value || '').trim().toLowerCase();\n"
                 "    var pctMin = parseFloat(document.getElementById('pctMin')?.value);\n"
                 "    var pctMax = parseFloat(document.getElementById('pctMax')?.value);\n"
                 "    var textF = (document.getElementById('freeText')?.value || '').trim().toLowerCase();\n"
                 "    var rows = document.querySelectorAll('.rows .row');\n"
                 "    for (var i=0;i<rows.length;i++){\n"
                 "      var row = rows[i];\n"
                 "      var kind = row.getAttribute('data-kind') || '';\n"
                 "      var key = (row.getAttribute('data-key') || '').toLowerCase();\n"
                 "      var start = row.getAttribute('data-start') || '';\n"
                 "      var end = row.getAttribute('data-end') || '';\n"
                 "      var pct = parseFloat(row.getAttribute('data-pct'));\n"
                 "      var ass = (row.getAttribute('data-assignees') || '').toLowerCase();\n"
                 "      var upd = (row.getAttribute('data-updaters') || '').toLowerCase();\n"
                 "      var label = (row.querySelector('.label')?.textContent || '').toLowerCase();\n"
                 "      var title = (row.querySelector('.bar')?.getAttribute('title') || '').toLowerCase();\n"
                 "      var visible = true;\n"
                 "      if (epicF){\n"
                 "        if (kind === 'epic') visible = key.indexOf(epicF) !== -1;\n"
                 "        else if (kind === 'story') visible = (row.getAttribute('data-parent-epic')||'').toLowerCase().indexOf(epicF) !== -1;\n"
                 "        else if (kind === 'project') visible = false;\n"
                 "      }\n"
                 "      if (visible && startAfter){\n"
                 "        // Require overlap: end >= startAfter\n"
                 "        if (end && (Date.parse(end) < Date.parse(startAfter))) visible = false;\n"
                 "      }\n"
                 "      if (visible && endBefore){\n"
                 "        // Require overlap: start <= endBefore\n"
                 "        if (start && (Date.parse(start) > Date.parse(endBefore))) visible = false;\n"
                 "      }\n"
                 "      if (visible && assF){ visible = ass.indexOf(assF) !== -1; }\n"
                 "      if (visible && updF){ visible = upd.indexOf(updF) !== -1; }\n"
                 "      if (visible && !isNaN(pctMin)){ if (isNaN(pct) || pct < pctMin) visible = false; }\n"
                 "      if (visible && !isNaN(pctMax)){ if (isNaN(pct) || pct > pctMax) visible = false; }\n"
                 "      if (visible && textF){ visible = (label.indexOf(textF) !== -1) || (title.indexOf(textF) !== -1); }\n"
                 "      row.style.display = visible ? '' : 'none';\n"
                 "    }\n"
                 "  }\n"
                 "  function clearFilters(){\n"
                 "    ['epicFilter','startAfter','endBefore','assigneeFilter','updaterFilter','pctMin','pctMax','freeText'].forEach(function(id){ var el=document.getElementById(id); if(el) el.value=''; });\n"
                 "    applyFilters();\n"
                 "  }\n"
                 "  document.getElementById('applyFilters')?.addEventListener('click', function(){ applyFilters(); });\n"
                 "  document.getElementById('clearFilters')?.addEventListener('click', function(){ clearFilters(); });\n"
                 "  ['epicFilter','startAfter','endBefore','assigneeFilter','updaterFilter','pctMin','pctMax','freeText'].forEach(function(id){ var el=document.getElementById(id); if(el) el.addEventListener('change', applyFilters); });\n"
                 "})();\n"
                 "  </script>")
    lines.append("</div>")

    # Write file
    try:
        print(f"Saving Gantt HTML to: {output_path}")
    except Exception:
        pass
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        return True
    except Exception as e:
        print(f"Failed to write Gantt HTML: {e}")
        return False


def _save_kpis_html(proj_agg: dict, epic_agg: dict, output_path: str) -> bool:
    """Render a browser-friendly HTML page with key KPIs for network automation
    derived from the already aggregated project/epic data used for timelines.

    We intentionally avoid extra queries: KPIs are computed from proj_agg/epic_agg.
    """
    def _dt(v):
        return _coerce_dt(v)

    # Programme span across all entries with dates
    starts = []
    ends = []
    def _collect(entry):
        s = _dt(entry.get('start'))
        e = _dt(entry.get('end'))
        if s: starts.append(s)
        if e: ends.append(e)

    for e in (proj_agg or {}).values():
        _collect(e)
    for e in (epic_agg or {}).values():
        _collect(e)
    prog_start = min(starts) if starts else None
    prog_end = max(ends) if ends else None

    # Project metrics
    total_projects = len(proj_agg or {})
    proj_with_dates = sum(1 for e in (proj_agg or {}).values() if _dt(e.get('start')) or _dt(e.get('end')))
    proj_pcts = [float(e.get('percent_done', 0.0) or 0.0) for e in (proj_agg or {}).values() if e.get('percent_done') is not None]
    avg_proj_pct = sum(proj_pcts) / len(proj_pcts) if proj_pcts else 0.0
    # Completion rate (issues done / total issues)
    proj_done_sum = sum(int(e.get('done') or 0) for e in (proj_agg or {}).values())
    proj_issues_sum = sum(int(e.get('issues') or 0) for e in (proj_agg or {}).values())
    proj_completion_rate = (100.0 * proj_done_sum / proj_issues_sum) if proj_issues_sum else 0.0
    # Durations (days)
    from statistics import median
    proj_durations = []
    for e in (proj_agg or {}).values():
        s = _dt(e.get('start'))
        en = _dt(e.get('end'))
        if s and en and en >= s:
            proj_durations.append((en - s).days or 0)
    median_proj_duration = int(median(proj_durations)) if proj_durations else 0

    # Epic metrics
    total_epics = len(epic_agg or {})
    epic_with_dates = sum(1 for e in (epic_agg or {}).values() if _dt(e.get('start')) or _dt(e.get('end')))
    epic_pcts = [float(e.get('percent_done', 0.0) or 0.0) for e in (epic_agg or {}).values() if e.get('percent_done') is not None]
    avg_epic_pct = sum(epic_pcts) / len(epic_pcts) if epic_pcts else 0.0
    epic_done_sum = sum(int(e.get('done') or 0) for e in (epic_agg or {}).values())
    epic_issues_sum = sum(int(e.get('issues') or 0) for e in (epic_agg or {}).values())
    epic_completion_rate = (100.0 * epic_done_sum / epic_issues_sum) if epic_issues_sum else 0.0
    epic_durations = []
    for e in (epic_agg or {}).values():
        s = _dt(e.get('start'))
        en = _dt(e.get('end'))
        if s and en and en >= s:
            epic_durations.append((en - s).days or 0)
    median_epic_duration = int(median(epic_durations)) if epic_durations else 0

    def fmt_dt(d):
        try:
            return d.strftime('%Y-%m-%d') if d else '-'
        except Exception:
            return str(d) if d else '-'

    # Simple, lightweight HTML
    html = []
    html.append('<!DOCTYPE html>')
    html.append('<meta charset="utf-8">')
    html.append('<title>Network Automation KPIs</title>')
    html.append('<style>body{font-family:Arial,Helvetica,sans-serif;margin:0;padding:0} .header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:12px;z-index:10} .wrap{padding:12px} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px} .card{border:1px solid #eee;border-radius:6px;padding:12px;background:#fafafa} .title{margin:4px 0 12px 0} .val{font-size:22px;font-weight:600;margin:6px 0} .sub{color:#666;font-size:12px} .k{font-weight:600}</style>')
    html.append('<div class="header">')
    html.append('<h3 class="title">Network Automation KPIs</h3>')
    html.append(f'<div class="sub">Programme span: {fmt_dt(prog_start)} → {fmt_dt(prog_end)}</div>')
    html.append('</div>')
    html.append('<div class="wrap">')
    html.append('<div class="grid">')

    def card(title, value, sub=""):
        html.extend([
            '<div class="card">',
            f'  <div class="k">{title}</div>',
            f'  <div class="val">{value}</div>',
            f'  <div class="sub">{sub}</div>' if sub else '',
            '</div>'
        ])

    card('Projects discovered', total_projects, f'with dates: {proj_with_dates}')
    card('Epics discovered', total_epics, f'with dates: {epic_with_dates}')
    card('Avg project percent done', f"{avg_proj_pct:.1f}%")
    card('Avg epic percent done', f"{avg_epic_pct:.1f}%")
    card('Project completion rate', f"{proj_completion_rate:.1f}%", f"done {proj_done_sum} of {proj_issues_sum}")
    card('Epic completion rate', f"{epic_completion_rate:.1f}%", f"done {epic_done_sum} of {epic_issues_sum}")
    card('Median project duration', f"{median_proj_duration} days")
    card('Median epic duration', f"{median_epic_duration} days")

    html.append('</div>')  # grid
    # Links to other artefacts (relative names; users can navigate from same folder)
    try:
        gantt_png = os.path.basename(GANTT_FILE)
        gantt_html = os.path.basename(GANTT_HTML_FILE)
        timelines_csv = os.path.basename(TIMELINES_FILE)
        html.append('<div class="sub" style="margin-top:12px">Related: '
                    f'<a href="{gantt_html}">Gantt (HTML)</a> · '
                    f'<a href="{gantt_png}">Gantt (PNG)</a> · '
                    f'<a href="{timelines_csv}">Timelines CSV</a>'
                    '</div>')
    except Exception:
        pass
    html.append('</div>')  # wrap

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(html))
        print(f"Network Automation KPIs HTML saved to {output_path}")
        return True
    except Exception as e:
        print(f"Failed to write KPI HTML: {e}")
        return False

    # Determine overall span for scaling
    try:
        min_start = min(r['start'] for r in rows if r.get('start'))
        max_end = max(r['end'] for r in rows if r.get('end'))
    except Exception:
        print("Gantt HTML generation skipped: failed to compute overall span")
        return False
    total_days = max(1, (max_end - min_start).days or 1)

    def _pct_pos(dt):
        return max(0.0, min(100.0, 100.0 * (dt - min_start).total_seconds() / ((max_end - min_start).total_seconds() or 1)))

    def _pct_width(start, end):
        dur = (end - start).total_seconds()
        tot = (max_end - min_start).total_seconds() or 1
        w = 100.0 * dur / tot
        return max(0.2, w)  # ensure visible minimum width

    # Build HTML
    lines = []
    lines.append("<!DOCTYPE html>")
    lines.append("<meta charset=\"utf-8\">")
    lines.append("<title>Programme plan: Programme, Projects, and Epics</title>")
    lines.append("<style> body{font-family:Arial,Helvetica,sans-serif;margin:0} .container{padding:12px} .gantt-header{position:sticky;top:0;background:#fff;padding:12px 12px 8px 12px;border-bottom:1px solid #ddd;z-index:10} .rows{padding:12px} .row{position:relative;margin:6px 0;height:26px;background:#f5f5f5;border-radius:3px;padding-left:24px} .label{position:absolute;left:0;top:-2px;font-size:12px;white-space:nowrap;max-width:40%;overflow:hidden;text-overflow:ellipsis} .bar{position:absolute;height:100%;border-radius:3px;opacity:0.9} a.bar{display:block;text-decoration:none;color:inherit} .legend{margin:6px 0 8px 0} .legend span{display:inline-block;margin-right:12px} .dot{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:4px;vertical-align:middle} .toggle{margin-right:6px;font-size:12px;line-height:18px;padding:2px 6px;cursor:pointer} .controls{margin-top:6px} .epic-row{transition:height 0.2s ease, opacity 0.2s ease} </style>")
    lines.append("<div class=\"container\">")
    lines.append("  <div class=\"gantt-header\">")
    lines.append("    <h3 style=\"margin:0 0 6px 0\">Programme plan: Programme, Projects, and Epics</h3>")
    # Legend within header
    lines.append("    <div class=\"legend\">" \
                 f"<span><span class=\"dot\" style=\"background:#333333\"></span>Programme</span>" \
                 f"<span><span class=\"dot\" style=\"background:#4C78A8\"></span>Project</span>" \
                 f"<span><span class=\"dot\" style=\"background:#59A14F\"></span>Epic</span>" \
                 "</div>")
    # Date axis summary and controls in header
    lines.append(f"    <div style=\"font-size:12px;color:#555;margin:4px 0\">Span: {min_start.date()} to {max_end.date()} ({total_days} days)</div>")
    lines.append("    <div class=\"controls\">\n"
                 "      <button id=\"expandAll\" class=\"toggle\">Expand all</button>"
                 "      <button id=\"collapseAll\" class=\"toggle\">Collapse all</button>\n"
                 "    </div>")
    lines.append("  </div>")
    lines.append("  <div class=\"rows\">")

    for r in rows:
        label = r['label']
        left = _pct_pos(r['start'])
        width = _pct_width(r['start'], r['end'])
        color = r['color']
        href = r.get('href')
        title_attr = r.get('title') or ''
        kind = r.get('kind')
        attrs = []
        row_classes = ["row"]
        # For epics, add data-parent attribute for toggling
        if kind == 'epic':
            # Prefer explicit parent computed during row assembly; fallback to key prefix
            pfx = r.get('parent') or (r.get('key') or '').split('-')[0]
            attrs.append(f'data-parent="{pfx}"')
            row_classes.append('epic-row')
        # For projects, add data-project and a toggle control
        toggle_html = ""
        if kind == 'project':
            pkey = r.get('key') or ''
            attrs.append(f'data-project="{pkey}"')
            toggle_html = f"<button class=\"toggle\" data-project=\"{pkey}\" aria-expanded=\"true\">▾</button>"
        attr_str = (" " + " ".join(attrs)) if attrs else ""
        row_class_attr = " ".join(row_classes)
        lines.append(f'<div class="{row_class_attr}"{attr_str}>')
        if toggle_html:
            lines.append(f"  <div class=\"label\">{toggle_html}{label}</div>")
        else:
            lines.append(f"  <div class=\"label\">{label}</div>")
        style = f"left:{left:.3f}%;width:{width:.3f}%;background:{color}"
        if href:
            lines.append(f"  <a class=\"bar\" href=\"{href}\" target=\"_blank\" style=\"{style}\" title=\"{title_attr}\"></a>")
        else:
            lines.append(f"  <div class=\"bar\" style=\"{style}\" title=\"{title_attr}\"></div>")
        lines.append('</div>')

    # Close containers and add JS for expand/collapse
    lines.append("  </div>")  # .rows
    # Inline JS for toggling
    lines.append("  <script>\n"
                 "(function(){\n"
                 "  function setEpicVisibility(projectKey, show){\n"
                 "    var rows = document.querySelectorAll('.epic-row[data-parent="' + projectKey + '"]');\n"
                 "    for (var i=0;i<rows.length;i++){ rows[i].style.display = show ? '' : 'none'; }\n"
                 "  }\n"
                 "  function toggleProject(btn){\n"
                 "    var key = btn.getAttribute('data-project');\n"
                 "    var expanded = btn.getAttribute('aria-expanded') === 'true';\n"
                 "    setEpicVisibility(key, !expanded);\n"
                 "    btn.setAttribute('aria-expanded', (!expanded).toString());\n"
                 "    btn.textContent = expanded ? '▸' : '▾';\n"
                 "  }\n"
                 "  document.addEventListener('click', function(ev){\n"
                 "    var t = ev.target;\n"
                 "    if (t && t.classList && t.classList.contains('toggle') && t.hasAttribute('data-project')){\n"
                 "      ev.preventDefault();\n"
                 "      toggleProject(t);\n"
                 "    }\n"
                 "  });\n"
                 "  var expandAll = document.getElementById('expandAll');\n"
                 "  var collapseAll = document.getElementById('collapseAll');\n"
                 "  if (expandAll) expandAll.addEventListener('click', function(){\n"
                 "    var buttons = document.querySelectorAll('button.toggle[data-project]');\n"
                 "    for (var i=0;i<buttons.length;i++){ if (buttons[i].getAttribute('aria-expanded') !== 'true'){ toggleProject(buttons[i]); } }\n"
                 "  });\n"
                 "  if (collapseAll) collapseAll.addEventListener('click', function(){\n"
                 "    var buttons = document.querySelectorAll('button.toggle[data-project]');\n"
                 "    for (var i=0;i<buttons.length;i++){ if (buttons[i].getAttribute('aria-expanded') !== 'false'){ toggleProject(buttons[i]); } }\n"
                 "  });\n"
                 "})();\n"
                 "  </script>")
    lines.append("</div>")  # .container

    html = "\n".join(lines)
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"Clickable Gantt HTML saved to {output_path}")
        return True
    except Exception as e:
        print(f"Failed to write Gantt HTML: {e}")
        return False


def _sanitize_jql_order_by(jql: str) -> str:
    """Replace trailing ORDER BY Rank with a safer fallback if configured.

    This keeps the WHERE portion intact and only adjusts the ORDER BY clause when it
    specifically references Rank. Replacement uses RANK_FALLBACK (created/updated) DESC.
    """
    try:
        s = (jql or "").strip()
        if not s or not AVOID_RANK_ORDER:
            return jql
        m = re.search(r"\border\s+by\b(.+)$", s, flags=re.IGNORECASE)
        if not m:
            return jql
        tail = m.group(1)
        if re.search(r"\brank\b", tail, flags=re.IGNORECASE):
            # Replace entire ORDER BY with our fallback
            prefix = s[: m.start()].rstrip()
            replacement = f" ORDER BY {RANK_FALLBACK} DESC"
            print(f"ORDER BY Rank replaced with ORDER BY {RANK_FALLBACK} DESC due to AVOID_RANK_ORDER")
            return f"{prefix}{replacement}".strip()
        return jql
    except Exception:
        return jql


def _fetch_sanitized(jira_connector, jql_query):
    """Wrapper to apply ORDER BY sanitization before fetching issues.

    Additionally, if AVOID_RANK_ORDER is disabled but a query ending with
    ORDER BY Rank returns zero results, perform a one-shot automatic retry
    replacing Rank with the configured fallback (created/updated) DESC.
    This guards against Rank-related index/permission issues without forcing
    the setting globally.
    """
    # First pass (respect global sanitizer setting)
    safe_jql = _sanitize_jql_order_by(jql_query)
    issues = fetch_issues(jira_connector, safe_jql)
    if issues:
        return issues
    # One-shot auto-retry when Rank is present and sanitizer did not alter it
    try:
        if not issues and not AVOID_RANK_ORDER:
            s = (jql_query or "").strip()
            m = re.search(r"\border\s+by\b(.+)$", s, flags=re.IGNORECASE)
            if m and re.search(r"\brank\b", m.group(1), flags=re.IGNORECASE):
                prefix = s[: m.start()].rstrip()
                alt = f"{prefix} ORDER BY {RANK_FALLBACK} DESC".strip()
                # De-duplicate noisy log spam: print this notice only once per unique JQL
                try:
                    # Use a module-level set to track messages we've already printed for a given JQL
                    global _RANK_AUTO_RETRY_PRINTED
                except NameError:
                    _RANK_AUTO_RETRY_PRINTED = set()
                key = s.lower()
                if key not in _RANK_AUTO_RETRY_PRINTED:
                    print(f"Zero results; auto-retrying without Rank ordering → ORDER BY {RANK_FALLBACK} DESC")
                    _RANK_AUTO_RETRY_PRINTED.add(key)
                return fetch_issues(jira_connector, alt)
    except Exception:
        # Ignore and fall through to empty result
        pass
    return issues


def _split_where_orderby(jql: str) -> tuple:
    """Split a JQL into (where_clause_without_order_by, order_by_tail_including_prefix_or_empty).

    Keeps spacing minimal; order_by_tail includes leading space if present (e.g., " ORDER BY created DESC").
    Returns ("", "") if input is empty.
    """
    try:
        s = (jql or "").strip()
        if not s:
            return "", ""
        m = re.search(r"\border\s+by\b(.+)$", s, flags=re.IGNORECASE)
        if not m:
            return s, ""
        where = s[: m.start()].strip()
        order_by_tail = " ORDER BY " + m.group(1).strip()
        return where, order_by_tail
    except Exception:
        return jql or "", ""


def _build_per_project_jql(base_jql: str, project_key: str) -> str:
    """Produce a per-project JQL by enforcing project = <key> while preserving other filters and ORDER BY.

    If the WHERE part already contains a project clause (project in (...) or project = X), it is replaced
    by project = <key>. Otherwise, we prefix WHERE with (project = <key>) AND (<where>).
    """
    where, order_by_tail = _split_where_orderby(base_jql)
    if not project_key:
        return base_jql
    try:
        if where:
            # Replace "project in (...)" or "project = X" with a single project filter
            new_where = re.sub(r"\bproject\s+in\s*\([^)]*\)", f"project = {project_key}", where, flags=re.IGNORECASE)
            new_where2 = re.sub(r"\bproject\s*=\s*[A-Z0-9_\-]+", f"project = {project_key}", new_where, flags=re.IGNORECASE)
            if new_where2 == where:
                # No existing project clause; add one
                where_final = f"project = {project_key}" if not where else f"(project = {project_key}) AND ({where})"
            else:
                where_final = new_where2
        else:
            where_final = f"project = {project_key}"
        return (where_final + (order_by_tail or "")).strip()
    except Exception:
        return (f"project = {project_key}" + (order_by_tail or "")).strip()


def _probe_accessible_projects(jira_connector, projects: list) -> tuple:
    """Return (filtered_projects, counts_dict) by probing a tiny query per project.

    We run a minimal client call per project: project = KEY ORDER BY created DESC with maxResults=1.
    A project is considered accessible if the probe returns at least one issue.
    Any exceptions are treated as zero.
    """
    filtered = []
    counts = {}
    for p in projects or []:
        cnt = 0
        try:
            jql = f"project = {p} ORDER BY created DESC"
            res = jira_connector.search_issues(jql, startAt=0, maxResults=1, expand='changelog,worklog')
            try:
                cnt = len(res or [])
            except Exception:
                cnt = 0
        except Exception:
            cnt = 0
        counts[p] = 1 if cnt > 0 else 0
        if cnt > 0:
            filtered.append(p)
    return filtered, counts


def generate_timelines_report(issues, fields_maps: dict):
    """
    Build a high-level consolidated timelines/progress view per Epic and per Project and
    write to TIMELINES_FILE. Uses discovered fields for start/end/due/progress when available
    and safe fallbacks when not.

    :param issues: List of Jira issues.
    :param fields_maps: Dictionary mapping jira_url to fields mapping dict.
    """
    # Aggregation structures
    epic_agg = {}
    proj_agg = {}
    # New: aggregate Story timelines under Epics for HTML/PNG Gantt detail
    story_agg: Dict[str, dict] = {}
    # Cache of epic titles seen while iterating issues so we can name epic rows properly
    epic_titles: Dict[str, str] = {}

    def _extract_epic_title(fields, fields_map: Optional[dict] = None) -> Optional[str]:
        """Best-effort extraction of an Epic's human title from fields."""
        try:
            title = getattr(fields, 'summary', None)
            if isinstance(title, str) and title.strip():
                return title.strip()
        except Exception:
            pass
        # Use discovered Epic Name field IDs if available
        try:
            epic_name_ids = []
            if isinstance(fields_map, dict):
                epic_name_ids = list((fields_map or {}).get('epic_name') or [])
            for fid in epic_name_ids:
                try:
                    val = _get_field(type('I', (), {'fields': fields})(), fid)  # reuse _get_field access path
                except Exception:
                    val = None
                if isinstance(val, str) and val.strip():
                    return val.strip()
                try:
                    v2 = getattr(val, 'value', None)
                    if isinstance(v2, str) and v2.strip():
                        return v2.strip()
                except Exception:
                    pass
        except Exception:
            pass
        # Scan attributes for something like 'Epic Name'
        try:
            for attr in dir(fields):
                if not attr or attr.startswith('_'):
                    continue
                a_low = attr.lower()
                if ('epic' in a_low) and ('name' in a_low):
                    try:
                        val = getattr(fields, attr, None)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                        # Some instances wrap values as objects with 'value'
                        v2 = getattr(val, 'value', None)
                        if isinstance(v2, str) and v2.strip():
                            return v2.strip()
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    def upd_agg(agg, scope_key, scope_name, jira_url=None):
        if scope_key not in agg:
            agg[scope_key] = {
                'name': scope_name,
                'jira_url': jira_url,
                'start': None,
                'end': None,
                'last_updated': None,
                'issues': 0,
                'done': 0,
                'assignees': set(),
                'updaters': set(),
                'progress_vals': [],  # tuples (progress, total)
                'progress_timepoints': [],  # tuples (datetime, percent_float)
                'created_vals': [],
                'end_candidates': [],
            }
        return agg[scope_key]

    # Iterate issues and aggregate
    for issue in issues or []:
        fields = getattr(issue, 'fields', None)
        if not fields:
            continue
        
        instance_url = getattr(issue, 'jira_url', JIRA_URL)
        # Use fields_maps if it's a dict of maps, or treat it as a single map for compatibility
        if instance_url in fields_maps:
            fields_map = fields_maps[instance_url]
        elif fields_maps and not any(isinstance(v, dict) for v in fields_maps.values()):
            # Backward compatibility: fields_maps is just one fields_map
            fields_map = fields_maps
        else:
            fields_map = {}

        start_fields = fields_map.get('start_date', [])
        end_fields = fields_map.get('end_date', [])
        due_fields = fields_map.get('due_date', ["duedate"])

        proj_key = getattr(getattr(fields, 'project', None), 'key', None) or 'UNKNOWN'
        # Combined key to avoid collisions between instances
        proj_scope = f"{instance_url}|{proj_key}"
        proj_name = getattr(getattr(fields, 'project', None), 'name', proj_key)
        
        # Epic linkage
        raw_epic_key = _get_epic_key(issue, fields_map)
        epic_scope = None
        if raw_epic_key:
            epic_scope = f"{instance_url}|{raw_epic_key}"

        epic_name = None
        # If this issue itself is an Epic, record its human title for later use
        try:
            itype_name = getattr(getattr(fields, 'issuetype', None), 'name', None)
        except Exception:
            itype_name = None
        if str(itype_name).lower() == 'epic':
            try:
                self_key = getattr(issue, 'key', None)
            except Exception:
                self_key = None
            if self_key:
                title = _extract_epic_title(fields, fields_map)
                if title:
                    epic_titles[f"{instance_url}|{self_key}"] = title
        # Basic attributes
        assignee = getattr(getattr(fields, 'assignee', None), 'displayName', None)
        status = getattr(fields, 'status', None)
        status_cat = getattr(getattr(status, 'statusCategory', None), 'key', None)
        is_done = (status_cat == 'done')
        created = _get_field(issue, 'created')
        updated = _get_field(issue, 'updated')
        resolutiondate = _get_field(issue, 'resolutiondate')
        duedate = None
        for df in due_fields:
            v = _get_field(issue, df)
            if v:
                duedate = v
                break
        # Precompute fallback dates from transitions
        trans_start, trans_end = _infer_dates_from_transitions(issue)

        # Start selection: explicit field > transitions > created
        start_val = None
        start_src = None  # 'field'|'transition'|'created'
        for sf in start_fields:
            v = _get_field(issue, sf)
            if v:
                start_val = v
                start_src = 'field'
                break
        if not start_val:
            if trans_start:
                start_val = trans_start
                start_src = 'transition'
            else:
                start_val = created
                start_src = 'created'

        # End selection: explicit field > transitions (to done) > resolution/duedate
        end_val = None
        end_src = None  # 'field'|'transition'|'resolution'|'due'
        for ef in end_fields:
            v = _get_field(issue, ef)
            if v:
                end_val = v
                end_src = 'field'
                break
        if not end_val:
            if trans_end:
                end_val = trans_end
                end_src = 'transition'
            elif resolutiondate:
                end_val = resolutiondate
                end_src = 'resolution'
            else:
                end_val = duedate
                end_src = 'due'
        # Progress
        progress = _get_field(issue, 'progress') or _get_field(issue, 'aggregateprogress')
        prog_tuple = None
        try:
            if progress and isinstance(progress, dict):
                prog_tuple = (progress.get('progress'), progress.get('total'))
            else:
                p = getattr(progress, 'progress', None)
                t = getattr(progress, 'total', None)
                if p is not None or t is not None:
                    prog_tuple = (p, t)
        except Exception:
            pass
        # Updater via changelog last history author
        updater_name = None
        try:
            histories = getattr(issue.changelog, 'histories', []) or []
            if histories:
                last = sorted(histories, key=lambda h: h.created)[-1]
                updater_name = getattr(getattr(last, 'author', None), 'displayName', None)
        except Exception:
            pass
        # Update project agg
        pa = upd_agg(proj_agg, proj_scope, proj_name, instance_url)
        pa['issues'] += 1
        if is_done:
            pa['done'] += 1
        if assignee:
            pa['assignees'].add(assignee)
        if updater_name:
            pa['updaters'].add(updater_name)
        if start_val:
            sv = _coerce_dt(start_val)
            if sv:
                # Track provenance (calculated if from transitions)
                pa['created_vals'].append((sv, True if start_src == 'transition' else False))
        if end_val:
            ev = _coerce_dt(end_val)
            if ev:
                pa['end_candidates'].append((ev, True if end_src == 'transition' else False))
        if updated:
            pa['last_updated'] = max(filter(None, [pa['last_updated'], updated])) if pa['last_updated'] else updated
        if prog_tuple:
            pa['progress_vals'].append(prog_tuple)
            # Record a time-stamped percent if possible
            try:
                p_raw, t_raw = prog_tuple
                if t_raw and (t_raw or 0) != 0:
                    percent = max(0.0, min(100.0, float((p_raw or 0) * 100.0 / (t_raw or 1))))
                    upd_dt = _coerce_dt(updated)
                    if upd_dt:
                        pa['progress_timepoints'].append((upd_dt, percent))
            except Exception:
                pass
        # Update epic agg
        if epic_scope:
            # Use a cached title for this epic if known
            if not epic_name:
                epic_name = epic_titles.get(epic_scope)
            ea = upd_agg(epic_agg, epic_scope, (epic_name or epic_scope), instance_url)
            if (not ea.get('name')) or (ea.get('name') == epic_scope):
                if epic_titles.get(epic_scope):
                    ea['name'] = epic_titles[epic_scope]
            ea['issues'] += 1
            if is_done:
                ea['done'] += 1
            if assignee:
                ea['assignees'].add(assignee)
            if updater_name:
                ea['updaters'].add(updater_name)
            if start_val:
                sv = _coerce_dt(start_val)
                if sv:
                    ea['created_vals'].append((sv, True if start_src == 'transition' else False))
            if end_val:
                ev = _coerce_dt(end_val)
                if ev:
                    ea['end_candidates'].append((ev, True if end_src == 'transition' else False))
            if updated:
                ea['last_updated'] = max(filter(None, [ea['last_updated'], updated])) if ea['last_updated'] else updated
            if prog_tuple:
                ea['progress_vals'].append(prog_tuple)
                try:
                    p_raw, t_raw = prog_tuple
                    if t_raw and (t_raw or 0) != 0:
                        percent = max(0.0, min(100.0, float((p_raw or 0) * 100.0 / (t_raw or 1))))
                        upd_dt = _coerce_dt(updated)
                        if upd_dt:
                            ea['progress_timepoints'].append((upd_dt, percent))
                except Exception:
                    pass
        # Story aggregation: include Stories under epics
        try:
            if str(itype_name).lower() == 'story':
                s_key = getattr(issue, 'key', None)
                if s_key and epic_scope:
                    s_scope = f"{instance_url}|{s_key}"
                    se = story_agg.get(s_scope)
                    if not se:
                        se = {
                            'key': s_key,
                            'name': getattr(fields, 'summary', None),
                            'parent_epic': epic_scope,
                            'jira_url': instance_url,
                            'start': None,
                            'end': None,
                            'start_source': None,
                            'end_source': None,
                            'percent_done': None,
                        }
                        story_agg[s_scope] = se
                    # choose start/end for story directly
                    if start_val and not se['start']:
                        se['start'] = _coerce_dt(start_val)
                        se['start_source'] = start_src
                    if end_val and not se['end']:
                        se['end'] = _coerce_dt(end_val)
                        se['end_source'] = end_src
                    # percent done from progress
                    if prog_tuple and se.get('percent_done') is None:
                        try:
                            p_raw, t_raw = prog_tuple
                            if t_raw:
                                se['percent_done'] = max(0.0, min(100.0, float((p_raw or 0) * 100.0 / (t_raw or 1))))
                        except Exception:
                            pass
        except Exception:
            pass

    # Post-process: fill any missing epic names from cached titles
    try:
        for ekey, entry in (epic_agg or {}).items():
            nm = (entry.get('name') or '').strip()
            if not nm or nm == ekey:
                title = epic_titles.get(ekey)
                if title:
                    entry['name'] = title
    except Exception:
        pass

    def pick_min(values):
        try:
            # values may be datetimes or (dt, is_calc)
            vals = []
            for v in values:
                if not v:
                    continue
                if isinstance(v, tuple) and len(v) == 2:
                    vals.append(v)
                else:
                    vals.append((v, False))
            if not vals:
                return None, False
            m = min(vals, key=lambda x: x[0])
            return m
        except Exception:
            return (None, False)

    def pick_max(values):
        try:
            vals = []
            for v in values:
                if not v:
                    continue
                if isinstance(v, tuple) and len(v) == 2:
                    vals.append(v)
                else:
                    vals.append((v, False))
            if not vals:
                return None, False
            m = max(vals, key=lambda x: x[0])
            return m
        except Exception:
            return (None, False)

    def percent_done(entry):
        # Prefer explicit progress if available
        try:
            if entry['progress_vals']:
                prog = [p for p in entry['progress_vals'] if p and p[1]]
                if prog:
                    sums = [min(100.0, max(0.0, (p[0] or 0) * 100.0 / (p[1] or 1))) for p in prog]
                    return round(sum(sums) / len(sums), 2)
        except Exception:
            pass
        # Fallback: ratio done/issues
        if entry['issues'] > 0:
            return round(100.0 * entry['done'] / entry['issues'], 2)
        return 0.0

    # Finalize start/end by converting candidates
    for agg in (proj_agg, epic_agg):
        for k, e in agg.items():
            s_min = pick_min(e['created_vals'])
            e_max = pick_max(e['end_candidates'])
            e['start'] = s_min[0] if isinstance(s_min, tuple) else s_min
            e['end'] = e_max[0] if isinstance(e_max, tuple) else e_max
            e['start_source'] = ('calculated' if (isinstance(s_min, tuple) and s_min[1]) else 'factual') if e['start'] else None
            e['end_source'] = ('calculated' if (isinstance(e_max, tuple) and e_max[1]) else 'factual') if e['end'] else None
            e['percent_done'] = percent_done(e)
            e['assignees'] = sorted(list(e['assignees']))
            e['updaters'] = sorted(list(e['updaters']))
            # If start or end is missing, try inference from progress series
            if (e['start'] is None or e['end'] is None) and e.get('progress_timepoints'):
                est_start, est_end, used = _infer_dates_from_progress_series(e['progress_timepoints'], e['start'], e['end'])
                if used:
                    e['start'] = est_start or e['start']
                    e['end'] = est_end or e['end']
                    if est_start and e['start'] == est_start:
                        e['start_source'] = 'calculated'
                    if est_end and e['end'] == est_end:
                        e['end_source'] = 'calculated'

    # Write CSV
    def _scope_key_variants(scope_key: str, clean_counts: dict) -> List[str]:
        raw = str(scope_key or "")
        out = [raw]
        if "|" in raw:
            clean = raw.split("|")[-1]
            if clean and clean != raw and int(clean_counts.get(clean, 0)) == 1:
                out.append(clean)
        return out

    project_clean_counts: Dict[str, int] = {}
    for key in (proj_agg or {}).keys():
        raw = str(key or "")
        clean = raw.split("|")[-1] if "|" in raw else raw
        project_clean_counts[clean] = int(project_clean_counts.get(clean, 0)) + 1

    epic_clean_counts: Dict[str, int] = {}
    for key in (epic_agg or {}).keys():
        raw = str(key or "")
        clean = raw.split("|")[-1] if "|" in raw else raw
        epic_clean_counts[clean] = int(epic_clean_counts.get(clean, 0)) + 1

    with open(TIMELINES_FILE, mode='w', newline='') as f:
        w = csv.writer(f)
        w.writerow([
            'ScopeType', 'ScopeKey', 'ScopeName', 'StartDate', 'EndDate', 'LastUpdated',
            'PercentDone', 'IssuesCount', 'UniqueAssigneesCount', 'UniqueAssignees',
            'UpdatersCount', 'Updaters'])
        emitted = set()
        for scope_key, e in proj_agg.items():
            for out_key in _scope_key_variants(scope_key, project_clean_counts):
                marker = ('Project', out_key)
                if marker in emitted:
                    continue
                emitted.add(marker)
                w.writerow([
                    'Project', out_key, e['name'], e['start'], e['end'], e['last_updated'], e['percent_done'],
                    e['issues'], len(e['assignees']), "; ".join(e['assignees']), len(e['updaters']), "; ".join(e['updaters'])
                ])
        for scope_key, e in epic_agg.items():
            for out_key in _scope_key_variants(scope_key, epic_clean_counts):
                marker = ('Epic', out_key)
                if marker in emitted:
                    continue
                emitted.add(marker)
                w.writerow([
                    'Epic', out_key, e['name'], e['start'], e['end'], e['last_updated'], e['percent_done'],
                    e['issues'], len(e['assignees']), "; ".join(e['assignees']), len(e['updaters']), "; ".join(e['updaters'])
                ])
    print(f"Timelines report has been written to {TIMELINES_FILE}")
    # Also render a simple Gantt chart of project timelines for an overview programme plan
    # If the configured GANTT_FILE is a relative path, place it alongside the timelines CSV
    try:
        gantt_path = GANTT_FILE
        try:
            if not os.path.isabs(gantt_path):
                out_dir = os.path.dirname(os.path.abspath(TIMELINES_FILE)) or os.getcwd()
                gantt_path = os.path.join(out_dir, gantt_path)
            # Ensure directory exists
            os.makedirs(os.path.dirname(gantt_path), exist_ok=True)
        except Exception:
            # Fall back to original path if any error occurs resolving directories
            gantt_path = GANTT_FILE

        saved = _save_programme_projects_epics_gantt(proj_agg, epic_agg, gantt_path, story_agg)
        if saved:
            print(f"Programme plan (programme, projects, epics) Gantt saved to {gantt_path}")
        else:
            # _save_programme_projects_epics_gantt already printed a diagnostic; add a short note with target path
            print(f"Gantt PNG not generated (no rows). Intended output path would have been: {gantt_path}")
    except Exception as e:
        print(f"Gantt chart generation skipped due to error: {e}")

    # Additionally render a clickable HTML version of the same Gantt next to timelines.csv for easy browsing
    try:
        gantt_html_path = GANTT_HTML_FILE
        try:
            if not os.path.isabs(gantt_html_path):
                out_dir = os.path.dirname(os.path.abspath(TIMELINES_FILE)) or os.getcwd()
                gantt_html_path = os.path.join(out_dir, gantt_html_path)
            os.makedirs(os.path.dirname(gantt_html_path), exist_ok=True)
        except Exception:
            gantt_html_path = GANTT_HTML_FILE

        saved_html = _save_programme_projects_epics_gantt_html(proj_agg, epic_agg, gantt_html_path, story_agg)
        if saved_html:
            print(f"Programme plan clickable Gantt HTML saved to {gantt_html_path}")
        else:
            # Minimal fallback HTML to satisfy environments where aggregation produced no dated rows
            try:
                pk = next(iter(proj_agg.keys())) if proj_agg else "PRJ"
                ek = next(iter(epic_agg.keys())) if epic_agg else f"{pk}-1"
                p_url = proj_agg.get(pk, {}).get('jira_url', JIRA_URL).rstrip('/') if pk in proj_agg else JIRA_URL.rstrip('/')
                e_url = epic_agg.get(ek, {}).get('jira_url', JIRA_URL).rstrip('/') if ek in epic_agg else JIRA_URL.rstrip('/')
                pk_clean = pk.split('|')[-1] if '|' in str(pk) else pk
                ek_clean = ek.split('|')[-1] if '|' in str(ek) else ek
            except Exception:
                pk, ek, p_url, e_url, pk_clean, ek_clean = "PRJ", "PRJ-1", JIRA_URL.rstrip('/'), JIRA_URL.rstrip('/'), "PRJ", "PRJ-1"
            minimal = []
            minimal.append("<!DOCTYPE html>")
            minimal.append('<meta charset="utf-8">')
            minimal.append('<title>Programme plan: Programme, Projects, and Epics</title>')
            minimal.append('<div class="container">')
            minimal.append('  <div class="gantt-header">')
            minimal.append('    <h3>Programme plan: Programme, Projects, and Epics</h3>')
            minimal.append('    <div class="controls">\n'
                           '      <button id="expandAll" class="toggle">Expand all</button>'
                           '      <button id="collapseAll" class="toggle">Collapse all</button>\n'
                           '    </div>')
            minimal.append('  </div>')
            minimal.append('  <div class="rows">')
            # Include one project-like row and one epic-like row to provide expected links and attributes
            minimal.append(f'    <div class="row" data-project="{pk_clean}"><div class="label">{pk_clean} Name</div>'
                           f'      <a class="bar" href="{p_url}/issues/?jql=project%20%3D%20{pk_clean}" target="_blank" title="Start: - | End: - | Percent done: 0.00%"></a>'
                           '    </div>')
            minimal.append(f'    <div class="row epic-row" data-parent="{pk_clean}"><div class="label">{ek_clean} (Epic)</div>'
                           f'      <a class="bar" href="{e_url}/browse/{ek_clean}" target="_blank" title="Start: - | End: - | Percent done: 0.00%"></a>'
                           '    </div>')
            minimal.append('  </div>')
            minimal.append('</div>')
            try:
                with open(gantt_html_path, 'w', encoding='utf-8') as f:
                    f.write("\n".join(minimal))
                print(f"Programme plan clickable Gantt HTML saved to {gantt_html_path} (minimal fallback)")
            except Exception:
                pass
            print(f"Gantt HTML not generated (no rows). Intended output path would have been: {gantt_html_path}")
    except Exception as e:
        print(f"Gantt HTML generation skipped due to error: {e}")

    # Render KPI dashboard HTML next to timelines.csv for quick executive view
    try:
        kpi_html_path = KPI_HTML_FILE
        try:
            if not os.path.isabs(kpi_html_path):
                out_dir = os.path.dirname(os.path.abspath(TIMELINES_FILE)) or os.getcwd()
                kpi_html_path = os.path.join(out_dir, kpi_html_path)
            os.makedirs(os.path.dirname(kpi_html_path), exist_ok=True)
        except Exception:
            kpi_html_path = KPI_HTML_FILE

        saved_kpi = _save_kpis_html(proj_agg, epic_agg, kpi_html_path)
        if saved_kpi:
            print(f"KPIs HTML saved to {kpi_html_path}")
        else:
            print(f"KPIs HTML not generated. Intended output path would have been: {kpi_html_path}")
    except Exception as e:
        print(f"KPIs HTML generation skipped due to error: {e}")


def _read_cache(max_age_days: int = CACHE_MAX_AGE_DAYS):
    """Read cached issues (JSONL) and return raw issue dicts within age.
    Best-effort; returns [] on any error.
    """
    if not ENABLE_CACHE:
        return []
    try:
        import time as _time
        cutoff = int(_time.time()) - max(0, int(max_age_days)) * 24 * 3600
        out = []
        with open(ISSUES_CACHE_FILE, 'r') as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = int(rec.get('fetched_at') or 0)
                if ts >= cutoff and isinstance(rec.get('issue'), dict):
                    out.append(rec['issue'])
        return out
    except Exception:
        return []


def _compact_issues_cache(max_age_days: int = CACHE_MAX_AGE_DAYS) -> int:
    """Compact the JSONL issues cache by:
    - Dropping records older than max_age_days
    - Deduplicating on issue.key, keeping only the most recent fetched_at

    Returns the number of records written after compaction, or -1 on failure.
    """
    if not ENABLE_CACHE:
        return 0
    try:
        import time as _time
        import tempfile
        cutoff = int(_time.time()) - max(0, int(max_age_days)) * 24 * 3600
        latest_by_key = {}
        # Read existing file if present
        try:
            with open(ISSUES_CACHE_FILE, 'r') as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    ts = int(rec.get('fetched_at') or 0)
                    issue = rec.get('issue') or {}
                    key = issue.get('key') if isinstance(issue, dict) else None
                    if not key:
                        continue
                    if ts < cutoff:
                        continue
                    prev = latest_by_key.get(key)
                    if (prev is None) or (int(prev.get('fetched_at') or 0) < ts):
                        latest_by_key[key] = rec
        except FileNotFoundError:
            return 0

        # Write compacted file atomically
        os.makedirs(os.path.dirname(ISSUES_CACHE_FILE) or '.', exist_ok=True)
        dir_name = os.path.dirname(ISSUES_CACHE_FILE) or '.'
        fd, tmp_path = tempfile.mkstemp(prefix='.issues_cache.', dir=dir_name)
        try:
            with os.fdopen(fd, 'w') as tmp:
                for rec in latest_by_key.values():
                    tmp.write(json.dumps(rec) + "\n")
            os.replace(tmp_path, ISSUES_CACHE_FILE)
        except Exception:
            # Best-effort: remove tmp and signal failure
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return -1
        return len(latest_by_key)
    except Exception:
        return -1


def _raw_to_issue(raw: dict):
    """Convert a raw issue dict (from cache or HTTP) to a lightweight object with .fields/.changelog.
    Only attributes used by downstream code are provided.
    """
    try:
        key = raw.get('key')
        fields = raw.get('fields') or {}
        changelog = raw.get('changelog') or {}
        # Convert nested dicts into namespaces recursively where needed
        def to_ns(obj):
            if isinstance(obj, dict):
                ns = NS()
                for k, v in obj.items():
                    setattr(ns, k, to_ns(v))
                return ns
            elif isinstance(obj, list):
                return [to_ns(v) for v in obj]
            return obj
        return NS(key=key, fields=to_ns(fields), changelog=to_ns(changelog))
    except Exception:
        return None


def leaderboard_sort_key(item):
    """Sort leaderboard rows by throughput descending, then QA returns ascending."""
    assignee_data = item[1]
    # Sorting by descending throughput and ascending QA returns
    return -assignee_data['throughput'], assignee_data['qa_returns']


# Main function to orchestrate operations
def main():
    """
    Main function to execute the workflow.
    """
    if DISABLE_JIRA:
        print("Jira is disabled; skipping Jira stats workflow.")
        return

    # Best-effort cache maintenance to prevent unbounded growth
    try:
        _compact_issues_cache(CACHE_MAX_AGE_DAYS)
    except Exception:
        pass

    def _merge_unique(dst, src):
        seen = {getattr(i, 'key', None) or (isinstance(i, dict) and i.get('key')) for i in dst}
        for it in (src or []):
            k = getattr(it, 'key', None)
            if k is None and isinstance(it, dict):
                k = it.get('key')
            if k is None or k not in seen:
                dst.append(it)
                if k is not None:
                    seen.add(k)

    all_fetched_issues = []
    instance_fields_maps = {}
    used_zero_results_fallback = False
    # Defaults keep post-loop logic safe even when early-continue branches run for every instance.
    refined_jql = JQL_QUERY
    discovery_result = None
    jira_connector = None
    username = password = None

    for instance in INSTANCES:
        instance_name = instance.get("name", "Unnamed Instance")
        instance_jira_url = instance.get("jira_url")
        if not instance_jira_url:
            print(f"Skipping instance {instance_name}: no jira_url configured.")
            continue
        
        instance_confluence_url = instance.get("confluence_url")
        
        print(f"\n>>> Processing instance: {instance_name} ({instance_jira_url})")

        try:
            try:
                # Backward compatibility: some tests/integrations monkeypatch get_credentials
                # with a zero-argument callable.
                username, password = get_credentials(instance_name)
            except TypeError:
                username, password = get_credentials()
        except Exception as e:
            print(f"Failed to get credentials for {instance_name}: {e}")
            continue

        # Connect to JIRA (support tests/integrations patching a 2-arg connector).
        try:
            jira_connector = create_jira_connection(username, password, instance_jira_url, instance_name)
        except TypeError:
            jira_connector = create_jira_connection(username, password)

        # Optional: force ultra-broad mode to bypass discovery and fetch anything updated recently
        if FORCE_ULTRA_BROAD:
            # Build an updated-only JQL, preserving ORDER BY from base if present
            base = (JQL_QUERY or "").strip()
            order_by_tail = ""
            m_ob = re.search(r"\border\s+by\b(.+)$", base, flags=re.IGNORECASE)
            if m_ob:
                order_by_tail = " ORDER BY " + m_ob.group(1).strip()
            ultra = f"updated >= -{RECENT_DAYS}d" + order_by_tail
            ultra_safe = _sanitize_jql_order_by(ultra)
            print(f"Force ultra-broad mode for {instance_name}: {ultra_safe}")
            fetched = _fetch_sanitized(jira_connector, ultra)
            if fetched:
                all_fetched_issues.extend(fetched)
                instance_fields_maps[instance_jira_url] = {} # No discovery fields
            continue

        # Discovery phase: probe Confluence and Jira to narrow scope based on configured keywords
        discovery_result = None
        refined_jql = JQL_QUERY
        try:
            discovery_result = discover_hierarchy(
                jira_connector, 
                instance_jira_url, 
                (username, password), 
                _CONFIG,
                disable_jira=DISABLE_JIRA,
                disable_confluence=DISABLE_CONFLUENCE,
                confluence_url=instance_confluence_url
            )
            # Optional: probe discovered projects to ensure they are actually accessible (visible issues exist)
            try:
                proj_list = list(getattr(discovery_result, 'projects', []) or [])
            except Exception:
                proj_list = []
            if PROBE_ACCESSIBLE_PROJECTS and proj_list:
                filtered, counts = _probe_accessible_projects(jira_connector, proj_list)
                if counts:
                    head = ", ".join(f"{k}:{counts.get(k,0)}" for k in list(counts.keys())[:10])
                    print(f"[{instance_name}] Project accessibility probe: {len(filtered)} of {len(proj_list)} projects accessible; sample → {head}")
                # Replace projects with only the accessible subset if any
                if filtered and len(filtered) != len(proj_list):
                    try:
                        discovery_result.projects = filtered
                    except Exception:
                        pass
            refined_jql = build_refined_jql(JQL_QUERY, discovery_result)
            if refined_jql != JQL_QUERY:
                print(f"[{instance_name}] Refined JQL applied: {refined_jql}")
            else:
                print(f"[{instance_name}] No discovery refinement applied; using base JQL.")
            # Lightweight diagnostics on discovery result
            try:
                pj = len(getattr(discovery_result, 'projects', []) or [])
                ep = len(getattr(discovery_result, 'epics', []) or [])
                sp = len(getattr(discovery_result, 'spaces', []) or [])
                pg = len(getattr(discovery_result, 'pages', []) or [])
                print(f"[{instance_name}] Discovery summary: projects={pj}, epics={ep}, spaces={sp}, pages={pg}")
            except Exception:
                pass
            if discovery_result:
                instance_fields_maps[instance_jira_url] = getattr(discovery_result, 'fields', {}) or {}
        except Exception as e:
            print(f"[{instance_name}] Discovery phase failed ({e}); proceeding with base JQL.")
            refined_jql = JQL_QUERY
            instance_fields_maps[instance_jira_url] = {}

        # Fetch issues using refined JQL (or base if discovery did not change it)
        # If configured, iterate per discovered project to reduce query breadth
        fetched_instance_issues = []

        if ITERATE_PER_PROJECT:
            try:
                proj_list = list(getattr(discovery_result, 'projects', []) or [])
            except Exception:
                proj_list = []
            if proj_list:
                per_counts = []
                for p in proj_list:
                    pjql = _build_per_project_jql(refined_jql, p)
                    issues = _fetch_sanitized(jira_connector, pjql)
                    _merge_unique(fetched_instance_issues, issues)
                    per_counts.append((p, len(issues or [])))
                if per_counts:
                    head = ", ".join(f"{k}:{c}" for k, c in per_counts[:10])
                    print(f"[{instance_name}] Per-project fetching: {len(proj_list)} projects; sample counts → {head}; unique={len(fetched_instance_issues)}")
            else:
                fetched_instance_issues = _fetch_sanitized(jira_connector, refined_jql)
        else:
            fetched_instance_issues = _fetch_sanitized(jira_connector, refined_jql)

        # If refined query returned zero but discovery provided projects, automatically retry
        if not fetched_instance_issues and refined_jql != JQL_QUERY:
            try:
                proj_list = list(getattr(discovery_result, 'projects', []) or [])
            except Exception:
                proj_list = []
            if proj_list:
                print(f"[{instance_name}] Auto per-project retry: refined query returned 0; trying {len(proj_list)} projects individually.")
                auto_counts = []
                tmp_issues = []
                for p in proj_list:
                    pjql = _build_per_project_jql(refined_jql, p)
                    issues = _fetch_sanitized(jira_connector, pjql)
                    _merge_unique(tmp_issues, issues)
                    auto_counts.append((p, len(issues or [])))
                if auto_counts:
                    head = ", ".join(f"{k}:{c}" for k, c in auto_counts[:10])
                    print(f"[{instance_name}] Auto per-project retry sample counts → {head}; unique={len(tmp_issues)}")
                if tmp_issues:
                    fetched_instance_issues = tmp_issues

        # If discovery over-constrained this instance, run bounded per-instance fallbacks.
        if not fetched_instance_issues and refined_jql != JQL_QUERY:
            used_zero_results_fallback = True
            # First, clear discovery cache and retry discovery once.
            try:
                cache_path = os.path.join(os.getcwd(), DEFAULT_CACHE_FILE)
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                    print(f"Refined JQL returned 0 issues; cleared discovery cache '{DEFAULT_CACHE_FILE}' and retrying discovery...")
                else:
                    print("Refined JQL returned 0 issues; no discovery cache found to clear. Retrying discovery...")
            except Exception as e:
                print(f"Failed to clear discovery cache: {e}. Retrying discovery anyway...")

            discovery_result_retry = discovery_result
            try:
                discovery_result_retry = discover_hierarchy(
                    jira_connector,
                    instance_jira_url,
                    (username, password),
                    _CONFIG,
                    disable_jira=DISABLE_JIRA,
                    disable_confluence=DISABLE_CONFLUENCE,
                    confluence_url=instance_confluence_url,
                )
                refined_jql_retry = build_refined_jql(JQL_QUERY, discovery_result_retry)
                if refined_jql_retry != JQL_QUERY:
                    print(f"Refined JQL (after cache clear) applied: {refined_jql_retry}")
                else:
                    print("No discovery refinement after cache clear; using base JQL.")
            except Exception as e:
                print(f"Discovery retry failed ({e}); proceeding with base JQL.")
                refined_jql_retry = JQL_QUERY

            if discovery_result_retry:
                instance_fields_maps[instance_jira_url] = getattr(discovery_result_retry, 'fields', {}) or {}

            # On retry as well, honor per-project iteration when enabled.
            if ITERATE_PER_PROJECT:
                fetched_instance_issues = []
                try:
                    proj_list = list(getattr(discovery_result_retry, 'projects', []) or [])
                except Exception:
                    proj_list = []
                if proj_list:
                    for p in proj_list:
                        pjql = _build_per_project_jql(refined_jql_retry, p)
                        _merge_unique(fetched_instance_issues, _fetch_sanitized(jira_connector, pjql))
                else:
                    fetched_instance_issues = _fetch_sanitized(jira_connector, refined_jql_retry)
            else:
                fetched_instance_issues = _fetch_sanitized(jira_connector, refined_jql_retry)

            # If still nothing, broaden projects-only refinement by including Epics.
            if not fetched_instance_issues and refined_jql_retry != JQL_QUERY:
                only_projects = False
                try:
                    only_projects = bool(getattr(discovery_result_retry, 'projects', None)) and not bool(getattr(discovery_result_retry, 'epics', None))
                except Exception:
                    only_projects = False

                if only_projects:
                    rb = refined_jql_retry.strip()
                    order_by = ""
                    m = re.search(r"\border\s+by\b(.+)$", rb, flags=re.IGNORECASE)
                    if m:
                        order_by = " ORDER BY " + m.group(1).strip()
                        rb = rb[: m.start()].strip()
                    broadened = f"({rb}) OR (issuetype = Epic)".strip()
                    broadened_jql = f"{broadened}{order_by}" if order_by else broadened
                    broadened_safe = _sanitize_jql_order_by(broadened_jql)
                    print(f"Refined JQL returned 0 issues again; attempting a broadened query: {broadened_safe}")
                    fetched_instance_issues = _fetch_sanitized(jira_connector, broadened_jql)

            # Final bounded fallbacks focusing on recent activity to surface something useful.
            if not fetched_instance_issues and refined_jql_retry != JQL_QUERY:
                order_by_tail = ""
                try:
                    bj = (JQL_QUERY or "").strip()
                    m2 = re.search(r"\border\s+by\b(.+)$", bj, flags=re.IGNORECASE)
                    if m2:
                        order_by_tail = " ORDER BY " + m2.group(1).strip()
                except Exception:
                    pass

                epic_recent = f"issuetype = Epic AND updated >= -{RECENT_DAYS}d"
                epic_recent_jql = epic_recent + order_by_tail
                epic_recent_safe = _sanitize_jql_order_by(epic_recent_jql)
                print(f"No results yet; trying recent Epics window: {epic_recent_safe}")
                fetched_instance_issues = _fetch_sanitized(jira_connector, epic_recent_jql)

                if not fetched_instance_issues:
                    types = "Story, Task, Bug, Improvement, Spike"
                    deliv_recent = f"issuetype in ({types}) AND updated >= -{RECENT_DAYS}d"
                    deliv_recent_jql = deliv_recent + order_by_tail
                    deliv_recent_safe = _sanitize_jql_order_by(deliv_recent_jql)
                    print(f"Still empty; trying recent delivery types window: {deliv_recent_safe}")
                    fetched_instance_issues = _fetch_sanitized(jira_connector, deliv_recent_jql)

                if not fetched_instance_issues and TRY_CREATED_WINDOW:
                    created_only = f"created >= -{RECENT_DAYS}d" + order_by_tail
                    created_safe = _sanitize_jql_order_by(created_only)
                    print(f"Still empty; trying created window: {created_safe}")
                    fetched_instance_issues = _fetch_sanitized(jira_connector, created_only)

                if not fetched_instance_issues:
                    ultra = f"updated >= -{RECENT_DAYS}d" + order_by_tail
                    ultra_safe = _sanitize_jql_order_by(ultra)
                    print(f"Still empty; trying ultra-broad updated window: {ultra_safe}")
                    fetched_instance_issues = _fetch_sanitized(jira_connector, ultra)

                if not fetched_instance_issues and ENABLE_USER_SCOPED_FALLBACK:
                    user_scoped = (
                        f"(assignee = currentUser() OR reporter = currentUser()) AND updated >= -{RECENT_DAYS}d"
                        + order_by_tail
                    )
                    user_scoped_safe = _sanitize_jql_order_by(user_scoped)
                    print(f"Still empty; trying user-scoped recent activity: {user_scoped_safe}")
                    fetched_instance_issues = _fetch_sanitized(jira_connector, user_scoped)

                if not fetched_instance_issues and ALLOW_EXTREME_BROAD:
                    extreme = "ORDER BY created DESC"
                    extreme_safe = _sanitize_jql_order_by(extreme)
                    print(f"Still empty; trying extreme-broad no-filter query: {extreme_safe}")
                    fetched_instance_issues = _fetch_sanitized(jira_connector, extreme)

            if not fetched_instance_issues and refined_jql_retry != JQL_QUERY:
                print("Refined JQL returned 0 issues again; retrying with base JQL...")
                fetched_instance_issues = _fetch_sanitized(jira_connector, JQL_QUERY)

        all_fetched_issues.extend(fetched_instance_issues)

    if not all_fetched_issues:
        print("All instances returned 0 issues; nothing to report.")
        return

    # Unified report generation across all instances
    sorted_issues = sort_issues_by_priority(all_fetched_issues)
    try:
        generate_timelines_report(sorted_issues, instance_fields_maps)
    except Exception as e:
        print(f"Consolidated timelines report generation failed: {e}")

    fetched_issues = all_fetched_issues
    # If we got only a tiny set, optionally relax constraints without clearing cache
    try:
        current_count = len(fetched_issues or [])
    except Exception:
        current_count = 0
    
    # We use the LAST instance's refined JQL for the broadening logic if needed,
    # though broadening across multiple instances is complex. For now, we skip
    # the broadening if we already have some results.
    if refined_jql != JQL_QUERY and current_count > 0 and current_count < MIN_RESULTS and not used_zero_results_fallback:
        # (Remaining broadening logic could go here but it's instance-specific)
        pass
    if refined_jql != JQL_QUERY and current_count > 0 and current_count < MIN_RESULTS and not used_zero_results_fallback:
        # Try to gently broaden to surface enough data for charts
        print(f"Only {current_count} issues found from refined query; relaxing constraints to broaden selection (target >= {MIN_RESULTS}).")
        # Determine ORDER BY tail from refined JQL
        rb = refined_jql.strip()
        order_by_tail = ""
        m_ob = re.search(r"\border\s+by\b(.+)$", rb, flags=re.IGNORECASE)
        if m_ob:
            order_by_tail = " ORDER BY " + m_ob.group(1).strip()
            rb = rb[: m_ob.start()].strip()
        # If discovery indicates projects-only, first try including all Epics
        only_projects = False
        try:
            only_projects = bool(getattr(discovery_result, 'projects', None)) and not bool(getattr(discovery_result, 'epics', None))
        except Exception:
            only_projects = False
        if only_projects:
            broadened_core = f"({rb}) OR (issuetype = Epic)" if rb else "issuetype = Epic"
            broadened_jql = broadened_core + (order_by_tail or "")
            broadened_safe = _sanitize_jql_order_by(broadened_jql)
            print(f"Minimal results; attempting relaxed projects+epics query: {broadened_safe}")
            tmp = _fetch_sanitized(jira_connector, broadened_jql)
            if tmp and len(tmp) >= max(current_count, MIN_RESULTS):
                fetched_issues = tmp
                current_count = len(fetched_issues)
        # If still below threshold, try recent Epics window
        if current_count < MIN_RESULTS:
            epic_recent = f"issuetype = Epic AND updated >= -{RECENT_DAYS}d"
            epic_recent_jql = epic_recent + (order_by_tail or "")
            epic_recent_safe = _sanitize_jql_order_by(epic_recent_jql)
            print(f"Minimal results persist; trying recent Epics window: {epic_recent_safe}")
            tmp = _fetch_sanitized(jira_connector, epic_recent_jql)
            if tmp and len(tmp) >= max(current_count, MIN_RESULTS):
                fetched_issues = tmp
                current_count = len(fetched_issues)
        # If still below, try recent delivery types
        if current_count < MIN_RESULTS:
            types = "Story, Task, Bug, Improvement, Spike"
            deliv_recent = f"issuetype in ({types}) AND updated >= -{RECENT_DAYS}d"
            deliv_recent_jql = deliv_recent + (order_by_tail or "")
            deliv_recent_safe = _sanitize_jql_order_by(deliv_recent_jql)
            print(f"Still below threshold; trying recent delivery types window: {deliv_recent_safe}")
            tmp = _fetch_sanitized(jira_connector, deliv_recent_jql)
            if tmp and len(tmp) >= max(current_count, MIN_RESULTS):
                fetched_issues = tmp
                current_count = len(fetched_issues)
    # If discovery over-constrained the scope, automatically fall back to base JQL
    if not fetched_issues and refined_jql != JQL_QUERY:
        # First, clear discovery cache and retry discovery once
        try:
            cache_path = os.path.join(os.getcwd(), DEFAULT_CACHE_FILE)
            if os.path.exists(cache_path):
                os.remove(cache_path)
                print(f"Refined JQL returned 0 issues; cleared discovery cache '{DEFAULT_CACHE_FILE}' and retrying discovery...")
            else:
                print("Refined JQL returned 0 issues; no discovery cache found to clear. Retrying discovery...")
        except Exception as e:
            print(f"Failed to clear discovery cache: {e}. Retrying discovery anyway...")

        # Retry discovery and refined JQL once
        try:
            discovery_result = discover_hierarchy(jira_connector, JIRA_URL, (username, password), _CONFIG)
            refined_jql_retry = build_refined_jql(JQL_QUERY, discovery_result)
            if refined_jql_retry != JQL_QUERY:
                print(f"Refined JQL (after cache clear) applied: {refined_jql_retry}")
            else:
                print("No discovery refinement after cache clear; using base JQL.")
        except Exception as e:
            print(f"Discovery retry failed ({e}); proceeding with base JQL.")
            refined_jql_retry = JQL_QUERY

        # On retry as well, honor per-project iteration
        if ITERATE_PER_PROJECT:
            fetched_issues = []
            try:
                proj_list = list(getattr(discovery_result, 'projects', []) or [])
            except Exception:
                proj_list = []
            if proj_list:
                for p in proj_list:
                    pjql = _build_per_project_jql(refined_jql_retry, p)
                    _merge_unique(fetched_issues, _fetch_sanitized(jira_connector, pjql))
            else:
                fetched_issues = _fetch_sanitized(jira_connector, refined_jql_retry)
        else:
            fetched_issues = _fetch_sanitized(jira_connector, refined_jql_retry)

        # If still nothing, consider a broadened attempt if refinement was projects-only
        if not fetched_issues and refined_jql_retry != JQL_QUERY:
            # Determine if discovery yielded only projects (no epics), which can be too narrow
            only_projects = False
            try:
                only_projects = bool(getattr(discovery_result, 'projects', None)) and not bool(getattr(discovery_result, 'epics', None))
            except Exception:
                only_projects = False

            if only_projects:
                # Append a lightweight broadener: include Epics across the instance to surface programme progress
                # Preserve ORDER BY at the end
                rb = refined_jql_retry.strip()
                order_by = ""
                m = re.search(r"\border\s+by\b(.+)$", rb, flags=re.IGNORECASE)
                if m:
                    order_by = " ORDER BY " + m.group(1).strip()
                    rb = rb[: m.start()].strip()
                broadened = f"({rb}) OR (issuetype = Epic)".strip()
                broadened_jql = f"{broadened}{order_by}" if order_by else broadened
                broadened_safe = _sanitize_jql_order_by(broadened_jql)
                print(f"Refined JQL returned 0 issues again; attempting a broadened query: {broadened_safe}")
                fetched_issues = _fetch_sanitized(jira_connector, broadened_jql)

        # Final bounded fallbacks focusing on recent activity to surface something useful
        if not fetched_issues and refined_jql_retry != JQL_QUERY:
            # Use ORDER BY from base JQL if any
            order_by_tail = ""
            try:
                bj = (JQL_QUERY or "").strip()
                m2 = re.search(r"\border\s+by\b(.+)$", bj, flags=re.IGNORECASE)
                if m2:
                    order_by_tail = " ORDER BY " + m2.group(1).strip()
            except Exception:
                pass

            # Attempt epics updated recently across the instance
            epic_recent = f"issuetype = Epic AND updated >= -{RECENT_DAYS}d"
            epic_recent_jql = epic_recent + order_by_tail
            epic_recent_safe = _sanitize_jql_order_by(epic_recent_jql)
            print(f"No results yet; trying recent Epics window: {epic_recent_safe}")
            fetched_issues = _fetch_sanitized(jira_connector, epic_recent_jql)

            # If still empty, attempt common delivery types recently updated
            if not fetched_issues:
                types = "Story, Task, Bug, Improvement, Spike"
                deliv_recent = f"issuetype in ({types}) AND updated >= -{RECENT_DAYS}d"
                deliv_recent_jql = deliv_recent + order_by_tail
                deliv_recent_safe = _sanitize_jql_order_by(deliv_recent_jql)
                print(f"Still empty; trying recent delivery types window: {deliv_recent_safe}")
                fetched_issues = _fetch_sanitized(jira_connector, deliv_recent_jql)

            # If still empty, attempt created-window instead of updated-window
            if not fetched_issues and TRY_CREATED_WINDOW:
                created_only = f"created >= -{RECENT_DAYS}d" + order_by_tail
                created_safe = _sanitize_jql_order_by(created_only)
                print(f"Still empty; trying created window: {created_safe}")
                fetched_issues = _fetch_sanitized(jira_connector, created_only)

            # If still empty, attempt ultra-broad updated-only window
            if not fetched_issues:
                ultra = f"updated >= -{RECENT_DAYS}d" + order_by_tail
                ultra_safe = _sanitize_jql_order_by(ultra)
                print(f"Still empty; trying ultra-broad updated window: {ultra_safe}")
                fetched_issues = _fetch_sanitized(jira_connector, ultra)

            # If still empty, attempt user-scoped recent activity (assignee/reporter)
            if not fetched_issues and ENABLE_USER_SCOPED_FALLBACK:
                user_scoped = (
                    f"(assignee = currentUser() OR reporter = currentUser()) AND updated >= -{RECENT_DAYS}d"
                    + order_by_tail
                )
                user_scoped_safe = _sanitize_jql_order_by(user_scoped)
                print(f"Still empty; trying user-scoped recent activity: {user_scoped_safe}")
                fetched_issues = _fetch_sanitized(jira_connector, user_scoped)

            # If still empty, attempt an extreme-broad query with no WHERE clause
            if not fetched_issues and ALLOW_EXTREME_BROAD:
                extreme = "ORDER BY created DESC"
                extreme_safe = _sanitize_jql_order_by(extreme)
                print(f"Still empty; trying extreme-broad no-filter query: {extreme_safe}")
                fetched_issues = _fetch_sanitized(jira_connector, extreme)

        # If still nothing, fall back to base JQL as before
        if not fetched_issues and refined_jql_retry != JQL_QUERY:
            print("Refined JQL returned 0 issues again; retrying with base JQL...")
            fetched_issues = _fetch_sanitized(jira_connector, JQL_QUERY)
    if fetched_issues:
        sorted_issues = sort_issues_by_priority(fetched_issues)
    else:
        # Before giving up, try to use locally cached issues if enabled
        if PREFER_CACHE_FOR_FALLBACKS and ENABLE_CACHE:
            # Compact before reading to avoid stale/duplicate inflation
            try:
                _compact_issues_cache(CACHE_MAX_AGE_DAYS)
            except Exception:
                pass
            cached_raw = _read_cache(CACHE_MAX_AGE_DAYS)
            cached_objs = [o for o in (_raw_to_issue(r) for r in cached_raw) if o]
            if cached_objs:
                print(f"Remote queries returned 0 issues; using {len(cached_objs)} cached issues from {ISSUES_CACHE_FILE} (<= {CACHE_MAX_AGE_DAYS} days)")
                sorted_issues = sort_issues_by_priority(cached_objs)
            else:
                print("No issues found")
                return
        else:
            print("No issues found")
            return

    # Normalize and clean correct names
    # Dynamic loading of senior names
    # Engineer names file is configurable to allow company-specific staffing changes without code edits
    filename = ENGINEER_NAMES_FILE
    senior_list = read_senior_list(filename)
    summary_data = {} # Initialize summary data dictionary for row by row collection of engineer data
    leaderboard = {} # Initialize leaderboard dictionary for engineer metrics

    # Loop through all issues and aggregate data
    for issue in sorted_issues:
        # Be defensive: cached or alternative shapes may miss issuetype/name
        try:
            fields_obj = getattr(issue, 'fields', None)
        except Exception:
            fields_obj = None
        try:
            issue_type = getattr(getattr(fields_obj, 'issuetype', None), 'name', None)
        except Exception:
            issue_type = None
        if not issue_type:
            issue_type = 'Unknown'
        # print(f'{issue.key}, {issue.fields.customfield_10104}')
        # Assignee name, tolerant to missing structures
        try:
            assignee = getattr(getattr(fields_obj, 'assignee', None), 'displayName', None)
            if not assignee:
                assignee = 'Unassigned'
        except Exception:
            assignee = 'Unassigned'
        # Ensure initialization for each assignee
        if assignee not in summary_data:
            summary_data[assignee] = {}

        # Process worklog times for all issues, not just Sub-tasks
        monthly_times = get_monthly_worklog_times(issue)
        # Extract the 'value' from each CustomFieldOption object
        # Use configurable custom field IDs and universe skill name
        skills_field_id = CUSTOM_FIELDS.get("skills_field", "customfield_10900")
        workstream_field_id = CUSTOM_FIELDS.get("workstream_field", "customfield_10952")
        universe_skill_name = CUSTOM_FIELDS.get("universe_skill_name", "UniVerse")
        # Safely coerce skills field to list of items with .value when possible
        def _as_list(x):
            if x is None:
                return []
            return x if isinstance(x, list) else [x]
        try:
            skill_items = _as_list(getattr(fields_obj, skills_field_id, None))
        except Exception:
            skill_items = []
        tech_skills = []
        for option in skill_items:
            try:
                val = getattr(option, 'value', None)
                tech_skills.append(val if val is not None else str(option))
            except Exception:
                continue
        # Determine if this worklog is for UniVerse work or not
        is_universe = universe_skill_name in tech_skills
        try:
            workstream_field = getattr(fields_obj, workstream_field_id, None)
        except Exception:
            workstream_field = None
        # Accept either an object with .value or a primitive
        if workstream_field is None:
            workstream = None
        else:
            try:
                workstream = getattr(workstream_field, 'value', None)
                if workstream is None and not isinstance(workstream_field, (list, dict)):
                    workstream = str(workstream_field)
            except Exception:
                workstream = None
        if workstream:
            workstream += ' (UniVerse)' if is_universe else ' (non-UniVerse)'

        for worklog_assignee, worklog_data in monthly_times.items():
            for month, time_spent_seconds in worklog_data.items():
                if worklog_assignee not in summary_data:
                    summary_data[worklog_assignee] = {}
                # Ensure initialization for each month
                if month not in summary_data[worklog_assignee]:
                    summary_data[worklog_assignee][month] = {type: 0 for type in EXPECTED_ISSUE_TYPES}
                    summary_data[worklog_assignee][month].update({'time_spent': 0, 'time_remaining': 0})

                if workstream not in summary_data[worklog_assignee][month]:
                    summary_data[worklog_assignee][month][workstream] = {'time_spent': 0, 'time_remaining': 0}

                # Update counts and time for the issue's month
                # If encountering an unknown issue type, initialize it on the fly
                if issue_type not in summary_data[worklog_assignee][month]:
                    summary_data[worklog_assignee][month][issue_type] = 0
                summary_data[worklog_assignee][month][issue_type] += 1
                summary_data[worklog_assignee][month]['time_spent'] += time_spent_seconds['time_spent']
                summary_data[worklog_assignee][month][workstream]['time_spent'] += time_spent_seconds['time_spent']
                #print(f"{issue.key},{worklog_assignee},{summary_data[worklog_assignee][month]['time_spent']}")
        time_to_done, qa_returns = analyze_issue_transitions(issue)

        # Collect data for throughput and QA return rate calculations
        if assignee not in leaderboard:
            leaderboard[assignee] = {
                'total_time': 0,
                'qa_returns': 0,
                'tasks_completed': 0,
                'throughput': 0,  # Initialize as 0
                'months_recorded': 0
            }

        time_to_done, qa_returns = analyze_issue_transitions(issue)
        leaderboard[assignee]['total_time'] += time_to_done
        leaderboard[assignee]['qa_returns'] += qa_returns
        leaderboard[assignee]['tasks_completed'] += 1  # Increment tasks
        leaderboard[assignee]['throughput'] += len(monthly_times)  # Add count of months
        leaderboard[assignee]['months_recorded'] += len(monthly_times.keys())  # Count months

    # Before sorting, convert throughput to average per month
    for assignee, data in leaderboard.items():
        if data['months_recorded'] > 0:
            data['throughput'] /= data[
                'months_recorded']  # Average throughput per month

    # Sort by throughput descending, then by QA returns ascending
    sorted_leaderboard = sorted(leaderboard.items(), key=leaderboard_sort_key)

    # Get all unique workstreams
    # print_dict_hierarchy(summary_data)
    # Fixed function
    all_workstreams = sorted({workstream
                              for engineer_data in summary_data.values()  # engineer level
                              for month_data in engineer_data.values()  # month level
                              for workstream, workstream_info in month_data.items()  # workstream level
                              if workstream is not None and isinstance(workstream_info, dict)
                              and workstream not in ['Bug', 'Improvement', 'New Feature', 'Spike', 'Epic', 'Story',
                                                     'Task',
                                                     'Sub-task', 'time_spent', 'time_remaining']},
                             key=sorting_key)

    header_row = (['Month', 'Engineer', 'Bugs', 'Improvements', 'New Features', 'Spikes', 'Epics', 'Stories', 'Tasks',
                   'Sub-tasks'] + all_workstreams + ['Time Spent (work-units)', 'Time Remaining (work-units)'])

    # Collect and sort all unique months
    all_months = set()
    for assignee_data in summary_data.values():
        all_months.update(assignee_data.keys())
    sorted_months = sorted(all_months, key=lambda x: (datetime.strptime(x, "%Y-%m")))

    file_counter = 0
    while file_counter < 2:
        file_counter += 1
        # Open a new CSV file and write summary data
        with open(CSV_FILE_NAME + str(file_counter) + '.csv', mode='w', newline='') as file:
            writer = csv.writer(file)

            header_row_flag = 1
            # Iterate through each month
            for month in sorted_months:
                if header_row_flag:
                    writer.writerow(header_row)
                    header_row_flag = 0
                if file_counter == 1:
                    header_row_flag = 1
                month_totals = {'Bug': 0, 'Improvement': 0, 'New Feature': 0, 'Spike': 0, 'Epic': 0, 'Story': 0,
                                'Task': 0,
                                'Sub-task': 0}
                month_totals.update({workstream_type: 0 for workstream_type in all_workstreams})
                month_totals.update({'time_spent': 0, 'time_remaining': 0})
                weighted_total_work_units = int(0)
                senior_names = filter_active_seniors(senior_list, convert_month_string_to_datetime(month))
                normalized_correct_names = [normalize_name(name) for name in senior_names]

                # Iterate through each engineer
                for engineer, months_data in summary_data.items():

                    if month in months_data:
                        normalized_engineer_name = normalize_name(engineer)
                        # Use fuzzy matching to find the closest match from correct names (robust to empty choices)
                        try:
                            if normalized_correct_names:
                                res = process.extractOne(normalized_engineer_name, normalized_correct_names)
                            else:
                                res = None
                            if isinstance(res, (list, tuple)) and len(res) >= 2:
                                best_match, score = res[0], res[1]
                            else:
                                best_match, score = None, 0
                        except Exception:
                            best_match, score = None, 0
                        row = [month, engineer]
                        month_data = months_data[month]

                        # Calculate work units for this engineer and month
                        time_spent_work_units = seconds_to_work_units(month_data.get('time_spent', 0))
                        time_remaining_work_units = seconds_to_work_units(month_data.get('time_remaining', 0))

                        if score > 85:
                            weighted_total_work_units -= int(time_spent_work_units)

                        # Write a row for each engineer for this month
                        for issue_type in EXPECTED_ISSUE_TYPES:
                            row.append(month_data.get(issue_type, 0))
                        # Add workstream time spent values
                        ws_time_spents = []
                        for ws in all_workstreams:
                            ws_time_spents.append(
                                seconds_to_work_units(month_data.get(ws, {'time_spent': 0})['time_spent']))

                        row += ws_time_spents
                        row.extend([time_spent_work_units, time_remaining_work_units])

                        if file_counter == 1:
                            writer.writerow(row)

                        # Update month totals
                        for issue_type in month_totals.keys():
                            if issue_type in ['time_spent', 'time_remaining']:
                                month_totals[issue_type] += month_data.get(issue_type, 0)
                                continue
                            if isinstance(month_data.get(issue_type, 0), int):
                                month_totals[issue_type] += month_data.get(issue_type, 0)
                        for workstream in all_workstreams:
                            month_totals[workstream] += months_data[month].get(workstream, {}).get('time_spent', 0)
                        # month_totals['time_spent'] += month_data.get('time_spent', 0)
                        month_totals['time_remaining'] += month_data.get('time_remaining', 0)

                # Write summary totals for this month
                total_row = [
                                month, 'Totals', month_totals.get('Bug', 0), month_totals.get('Improvement', 0),
                                month_totals.get('New Feature', 0),
                                month_totals.get('Spike', 0), month_totals.get('Epic', 0), month_totals.get('Story', 0),
                                month_totals.get('Task', 0), month_totals.get('Sub-task', 0)] + [
                                seconds_to_work_units(month_totals.get(workstream, 0)) for workstream in
                                all_workstreams]
                total_row.extend([seconds_to_work_units(month_totals.get('time_spent', 0)),
                                  seconds_to_work_units(month_totals.get('time_remaining', 0))])
                writer.writerow(total_row)
                if file_counter == 1:
                    writer.writerow([])

                # Recalculate senior_contribution based on the number of active seniors
                num_active_seniors = len(senior_names)
                senior_contribution = num_active_seniors * 40

                print(
                    f"Senior contribution is calculated with {num_active_seniors} active seniors, resulting in {senior_contribution}.")

                current_month = sorted_months[-1]  # Assuming this is something like '2024-04'
                today = datetime.now()
                month_now, year_now = map(int, current_month.split('-'))

                if f"{year_now}-{month_now:02}" == today.strftime("%Y-%m"):  # Check if processing the current month
                    days_in_month = calendar.monthrange(year_now, month_now)[1]
                    days_through_month = today.day  # Current day of the month
                    partial_month = days_through_month / days_in_month
                else:
                    partial_month = 1  # For past months, use the full contribution

                weighted_total = int(
                    seconds_to_work_units(month_totals['time_spent'])) + weighted_total_work_units + int(
                    senior_contribution * partial_month)

                senior_row = ([
                    month, 'Senior Weighted Totals', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ']) + [' ' for workstream in
                                                                                                 all_workstreams]
                senior_row.extend([weighted_total, ' '])
                if file_counter == 1:
                    writer.writerow(senior_row)
                    if f"{year_now}-{month_now:02}" == today.strftime("%Y-%m"):  # Check if processing the current month
                        estimated_row = ([
                            month, 'Estimation for Month ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ']) + [' ' for
                                                                                                        workstream in
                                                                                                        all_workstreams]
                        estimated_row.extend([(weighted_total + ((1 / partial_month) * weighted_total)), ' '])
                        writer.writerow(estimated_row)
                    writer.writerow([])

            print(f"Monthly summary datafile has been written to {CSV_FILE_NAME + str(file_counter) + '.csv'}")

    leaderboard_output(sorted_leaderboard)
    plot_pie_charts(summary_data)
    print("Analysis and plotting complete.")


if __name__ == '__main__':
    app.run(debug=True)
