import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_admin_control_room_queue_defaults_to_all_jobs_scope(monkeypatch):
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "pbisaacs")
    monkeypatch.setattr(refiner_web, "_user_role", lambda user: "admin" if user == "pbisaacs" else "user")

    with refiner_web.app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'class="ghost active" data-scope="all">All Jobs</button>' in html
    assert 'class="ghost" data-scope="team">Team</button>' in html


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_admin_all_jobs_scope_includes_conductor_owned_jobs(monkeypatch):
    jobs = [
        refiner_web.Job(
            job_id="job-owned-by-user",
            payload={"workflow": "project_solver", "token_scope": "personal"},
            project_name="Admin job",
            owner="pbisaacs",
        ),
        refiner_web.Job(
            job_id="job-owned-by-conductor",
            payload={"workflow": "project_solver", "token_scope": "personal"},
            project_name="Conductor job",
            owner="svc_conductor",
        ),
    ]

    monkeypatch.setattr(refiner_web, "_current_user", lambda: "pbisaacs")
    monkeypatch.setattr(refiner_web, "_is_admin_user", lambda user: user == "pbisaacs")
    monkeypatch.setattr(refiner_web.manager, "list_jobs", lambda status=None: jobs)

    with refiner_web.app.test_client() as client:
        all_response = client.get("/api/jobs?scope=all")
        personal_response = client.get("/api/jobs?scope=personal")

    assert all_response.status_code == 200
    all_job_ids = {item["id"] for item in all_response.get_json()["jobs"]}
    assert all_job_ids == {"job-owned-by-user", "job-owned-by-conductor"}

    assert personal_response.status_code == 200
    personal_job_ids = {item["id"] for item in personal_response.get_json()["jobs"]}
    assert personal_job_ids == {"job-owned-by-user"}
