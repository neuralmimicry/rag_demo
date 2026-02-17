import csv
from types import SimpleNamespace as NS

import main as m


def make_epic(epic_key: str, epic_name_field_id: str, epic_name_value: str):
    # Epic issue carrying the custom Epic Name field (no summary provided)
    fields = NS(
        project=NS(key=epic_key.split('-')[0], name=f"{epic_key.split('-')[0]} Name"),
        issuetype=NS(name="Epic"),
    )
    setattr(fields, epic_name_field_id, epic_name_value)
    return NS(key=epic_key, fields=fields, changelog=NS(histories=[]))


def make_child_issue(project_key: str, epic_key: str):
    # Story/Task linked to the epic via customfield_10014 (typical Epic Link)
    fields = NS(
        project=NS(key=project_key, name=f"{project_key} Name"),
        issuetype=NS(name="Story"),
        created="2025-01-01",
        duedate="2025-02-01",
    )
    setattr(fields, "customfield_10014", epic_key)
    return NS(fields=fields, changelog=NS(histories=[]))


def test_epic_title_extracted_from_custom_field(tmp_path, monkeypatch):
    # Arrange outputs
    timelines_csv = tmp_path / "timelines.csv"
    gantt_png = tmp_path / "gantt.png"
    gantt_html = tmp_path / "gantt.html"
    kpis_html = tmp_path / "kpis.html"
    monkeypatch.setattr(m, "TIMELINES_FILE", str(timelines_csv))
    monkeypatch.setattr(m, "GANTT_FILE", str(gantt_png))
    monkeypatch.setattr(m, "GANTT_HTML_FILE", str(gantt_html))
    monkeypatch.setattr(m, "KPI_HTML_FILE", str(kpis_html))

    # Epic CFNA-1184 carries Epic Name in a custom field (simulate e.g., customfield_10011)
    epic_key = "CFNA-1184"
    epic_name_field_id = "customfield_10011"
    epic_name_value = "OLT Turn up improvement"
    epic_issue = make_epic(epic_key, epic_name_field_id, epic_name_value)

    # Child issue linked to the epic ensures the epic shows up in aggregation
    child = make_child_issue("CFNA", epic_key)

    fields_map = {
        "start_date": [],
        "end_date": [],
        "due_date": ["duedate"],
        "epic_link": ["customfield_10014"],
        # Provide discovered Epic Name field id
        "epic_name": [epic_name_field_id],
    }

    # Act
    m.generate_timelines_report([epic_issue, child], fields_map)

    # Assert epic row exists with the proper Title/Name
    rows = list(csv.reader(timelines_csv.open()))
    # Header: ScopeType, ScopeKey, Name, Start, End, LastUpdated, PercentDone, Issues, UniqueAssigneesCount, Assignees, UpdatersCount, Updaters
    epic_rows = [r for r in rows[1:] if r and r[0] == "Epic" and r[1] == epic_key]
    assert epic_rows, "Expected an Epic row for the linked epic"
    assert epic_rows[0][2] == epic_name_value
