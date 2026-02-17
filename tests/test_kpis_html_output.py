import os
from types import SimpleNamespace as NS

import main as m


def make_issue(project_key="PRJ", created="2025-01-01", duedate="2025-02-01", done=False, epic_key=None):
    status = NS(statusCategory=NS(key=("done" if done else "indeterminate")))
    fields = NS(
        project=NS(key=project_key, name=f"{project_key} Name"),
        issuetype=NS(name="Story"),
        status=status,
        created=created,
        duedate=duedate,
    )
    if epic_key is not None:
        setattr(fields, "customfield_10014", epic_key)
    return NS(fields=fields, changelog=NS(histories=[]))


def test_kpis_html_is_created(tmp_path, monkeypatch):
    # Arrange output paths
    gantt_png = tmp_path / "gantt.png"
    gantt_html = tmp_path / "gantt.html"
    kpis_html = tmp_path / "kpis.html"
    timelines_csv = tmp_path / "timelines.csv"
    monkeypatch.setattr(m, "GANTT_FILE", str(gantt_png))
    monkeypatch.setattr(m, "GANTT_HTML_FILE", str(gantt_html))
    monkeypatch.setattr(m, "KPI_HTML_FILE", str(kpis_html))
    monkeypatch.setattr(m, "TIMELINES_FILE", str(timelines_csv))

    # Provide issues spanning a project and an epic
    issues = [
        make_issue(project_key="PRJ", created="2025-01-01", duedate="2025-02-01"),
        make_issue(project_key="PRJ", created="2025-01-10", duedate="2025-02-10", epic_key="PRJ-1"),
    ]
    fields_map = {"start_date": [], "end_date": [], "due_date": ["duedate"], "epic_link": ["customfield_10014"]}

    # Act
    m.generate_timelines_report(issues, fields_map)

    # Assert KPI file exists and is non-empty, and contains some key labels
    assert os.path.exists(kpis_html), "Expected a KPI HTML to be created"
    content = kpis_html.read_text(encoding="utf-8")
    assert "Network Automation KPIs" in content
    assert "Projects discovered" in content and "Epics discovered" in content
    assert "Avg epic percent done" in content
    # Also expect links to related artefacts
    assert "Gantt (HTML)" in content and "Timelines CSV" in content
