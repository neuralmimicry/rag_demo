import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

import requests
import shutil
from flask import Flask, Response, jsonify, render_template, request, redirect, session, url_for, send_from_directory, g
from werkzeug.security import check_password_hash, generate_password_hash

from llm_providers import get_provider, LLMError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "web", "public")
JOB_ROOT = os.getenv("REFINER_JOB_DIR", os.path.join(BASE_DIR, "job_data"))
PROJECTS_ROOT = os.path.join(JOB_ROOT, "projects")
SECRET_STORE_ROOT = os.path.join(JOB_ROOT, "secrets")
USERS_PATH = os.path.join(JOB_ROOT, "users.json")
WORKSPACE_ROOT = os.path.join(JOB_ROOT, "workspaces")
DEFAULT_FORK_ORG = os.getenv("REFINER_FORK_ORG", "neuralmimicry")
DEFAULT_WORKERS = int(os.getenv("REFINER_WORKERS", "2"))
DEFAULT_TAIL = int(os.getenv("REFINER_LOG_TAIL", "200"))
DEFAULT_REPO_PRIVATE = str(os.getenv("REFINER_REPO_PRIVATE", "1")).lower() in {"1", "true", "yes"}
REQUIREMENTS_MAX_BYTES = int(os.getenv("REFINER_REQUIREMENTS_SCAN_BYTES", "20000"))

app = Flask(__name__, static_folder="web/static", template_folder="web/templates")
app.secret_key = os.getenv("REFINER_SECRET_KEY") or os.urandom(32)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> List[str]:
    value = os.getenv(name, "")
    items = []
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        items.append(entry.rstrip("/"))
    return items


def _normalize_samesite(value: Optional[str]) -> str:
    if not value:
        return ""
    cleaned = value.strip().lower()
    if cleaned == "none":
        return "None"
    if cleaned == "lax":
        return "Lax"
    if cleaned == "strict":
        return "Strict"
    return ""


APP_START_TIME = time.time()

METRICS_PATH = (os.getenv("REFINER_METRICS_PATH", "/metrics") or "/metrics").strip()
if not METRICS_PATH.startswith("/"):
    METRICS_PATH = f"/{METRICS_PATH}"

try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

    PROMETHEUS_AVAILABLE = True
except Exception:
    PROMETHEUS_AVAILABLE = False

METRICS_ENABLED = _env_flag("REFINER_METRICS_ENABLED", True) and PROMETHEUS_AVAILABLE

if METRICS_ENABLED:
    REQUEST_COUNT = Counter(
        "refiner_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
    )
    REQUEST_LATENCY = Histogram(
        "refiner_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "path"],
    )
    INFLIGHT = Gauge("refiner_http_inflight_requests", "In-flight HTTP requests")
    JOBS_BY_STATUS = Gauge("refiner_jobs_total", "Jobs by status", ["status"])
    JOB_QUEUE_DEPTH = Gauge("refiner_job_queue_depth", "Job queue depth")
    WORKER_COUNT = Gauge("refiner_worker_threads", "Worker threads")
    UPTIME = Gauge("refiner_uptime_seconds", "Application uptime in seconds")


CORS_ORIGINS = _env_list("REFINER_CORS_ORIGINS")
CORS_ALLOW_HEADERS = ["Content-Type", "Authorization", "X-Requested-With"]
CORS_ALLOW_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]
CORS_MAX_AGE = int(os.getenv("REFINER_CORS_MAX_AGE", "600"))
API_BASE = os.getenv("REFINER_API_BASE", "").strip().rstrip("/")

COOKIE_SAMESITE = _normalize_samesite(os.getenv("REFINER_COOKIE_SAMESITE"))
if not COOKIE_SAMESITE:
    COOKIE_SAMESITE = "None" if CORS_ORIGINS else "Lax"
SECURE_COOKIES = _env_flag("REFINER_SECURE_COOKIES", COOKIE_SAMESITE == "None")
ENFORCE_HTTPS = _env_flag("REFINER_ENFORCE_HTTPS", False)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
)

if _env_flag("REFINER_TRUST_PROXY", False):
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


def _ensure_dirs() -> None:
    os.makedirs(JOB_ROOT, exist_ok=True)
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    os.makedirs(SECRET_STORE_ROOT, exist_ok=True)
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip())
    cleaned = cleaned.strip("-")
    return cleaned or "project"


SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\\-]{3,32}$")

GUARDRAIL_PATTERNS = {
    "violence": [
        r"\\b(make|build|assemble|design)\\b.*\\b(bomb|explosive|grenade)\\b",
        r"\\b(weapon|firearm|gun)\\b",
        r"\\bassassinate\\b",
    ],
    "malware": [
        r"\\b(ransomware|keylogger|malware|virus|trojan|rootkit)\\b",
        r"\\b(phishing|credential\\s+harvest)\\b",
    ],
    "illegal": [
        r"\\b(counterfeit|forgery|fake\\s+id)\\b",
        r"\\bcredit\\s+card\\s+fraud\\b",
        r"\\bdrug\\s+trafficking\\b",
    ],
    "self_harm": [
        r"\\b(suicide|self-harm|self harm)\\b",
    ],
}


class SecretStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self.data = data
        except Exception:
            self.data = {}

    def _write(self) -> None:
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def list_masked(self) -> List[Dict[str, str]]:
        with self.lock:
            items = []
            for name, entry in sorted(self.data.items()):
                value = entry.get("value") if isinstance(entry, dict) else ""
                masked = self._mask(value)
                updated_at = entry.get("updated_at") if isinstance(entry, dict) else None
                items.append({"name": name, "masked": masked, "updated_at": updated_at})
            return items

    def set(self, name: str, value: str) -> None:
        with self.lock:
            self.data[name] = {"value": value, "updated_at": _now_iso()}
            self._write()

    def delete(self, name: str) -> bool:
        with self.lock:
            if name not in self.data:
                return False
            self.data.pop(name, None)
            self._write()
            return True

    def get_env(self) -> Dict[str, str]:
        with self.lock:
            env = {}
            for name, entry in self.data.items():
                if isinstance(entry, dict) and entry.get("value"):
                    env[name] = entry["value"]
            return env

    @staticmethod
    def _mask(value: Optional[str]) -> str:
        if not value:
            return "not set"
        tail = value[-4:] if len(value) >= 4 else value
        return f"***{tail}"


class UserStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.users: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self.users = data
        except Exception:
            self.users = {}

    def _write(self) -> None:
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.users, handle, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def has_users(self) -> bool:
        with self.lock:
            return bool(self.users)

    def ensure_admin_from_env(self) -> None:
        admin_user = (os.getenv("REFINER_ADMIN_USER") or "").strip()
        admin_pass = (os.getenv("REFINER_ADMIN_PASS") or "").strip()
        if not admin_user or not admin_pass:
            return
        with self.lock:
            if self.users:
                return
            self.users[admin_user] = {
                "password": generate_password_hash(admin_pass),
                "created_at": _now_iso(),
                "role": "admin",
            }
            self._write()

    def create_user(self, username: str, password: str, role: str = "user") -> None:
        with self.lock:
            self.users[username] = {
                "password": generate_password_hash(password),
                "created_at": _now_iso(),
                "role": role,
            }
            self._write()

    def verify(self, username: str, password: str) -> bool:
        with self.lock:
            entry = self.users.get(username)
            if not entry:
                return False
            return check_password_hash(entry.get("password") or "", password)

    def get_role(self, username: str) -> Optional[str]:
        with self.lock:
            entry = self.users.get(username) or {}
            return entry.get("role")


TOKEN_RE = re.compile(
    r"Token usage \[(?P<category>[^\]]+)\] (?P<provider>[^/\s]+)/(?P<model>[^:]+): "
    r"sent=(?P<prompt>\?|\d+) received=(?P<completion>\?|\d+) used=(?P<total>\?|\d+)"
    r"(?: cached=(?P<cached>\?|\d+))? \| running total \[(?P<cat2>[^\]]+)\]: "
    r"sent=(?P<run_prompt>\d+) received=(?P<run_completion>\d+) used=(?P<run_total>\d+)"
)


@dataclass
class Stage:
    name: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    message: Optional[str] = None


@dataclass
class Job:
    job_id: str
    payload: Dict[str, Any]
    project_name: str = ""
    owner: str = ""
    status: str = "queued"
    workflow: str = "project_solver"
    progress: int = 0
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    restart_count: int = 0
    log_path: str = ""
    events_path: str = ""
    output_paths: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    stages: List[Stage] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    stop_requested: bool = False
    repo_info: Dict[str, Any] = field(default_factory=dict)
    log_buffer: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=2000))
    log_listeners: List[queue.Queue] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.workflow = self.payload.get("workflow") or self.workflow
        self.project_name = self._derive_project_name(self.payload)
        self.owner = self.payload.get("owner") or self.owner
        self.metrics = {
            "token_usage": {"prompt": 0, "completion": 0, "total": 0, "cached": None},
            "errors": 0,
            "resolved": 0,
            "warnings": 0,
            "queue_wait_sec": None,
            "runtime_sec": None,
        }

    @staticmethod
    def _derive_project_name(payload: Dict[str, Any]) -> str:
        name = (payload.get("project_name") or "").strip()
        if name:
            return name
        root = (payload.get("project_root") or payload.get("delivery_project_root") or "").strip()
        if root:
            return os.path.basename(root.rstrip("/")) or root
        space = (payload.get("space") or "").strip()
        if space:
            return f"Space {space}"
        projects = (payload.get("projects") or "").strip()
        if projects:
            return f"Projects {projects}"
        topic = (payload.get("topic_source") or payload.get("topic_research") or "").strip()
        if topic:
            return topic[:60] + ("…" if len(topic) > 60 else "")
        return "Untitled"

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status
            self.updated_at = _now_iso()

    def set_progress(self, progress: int) -> None:
        with self.lock:
            self.progress = max(0, min(100, int(progress)))
            self.updated_at = _now_iso()

    def add_listener(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.log_listeners.append(q)
        return q

    def remove_listener(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.log_listeners:
                self.log_listeners.remove(q)

    def append_log(self, line: str, stream: str = "stdout") -> None:
        entry = {"ts": _now_iso(), "line": line.rstrip("\n"), "stream": stream}
        with self.lock:
            self.log_buffer.append(entry)
            listeners = list(self.log_listeners)
        for listener in listeners:
            try:
                listener.put_nowait(entry)
            except queue.Full:
                continue

    def get_log_tail(self, count: int) -> List[Dict[str, Any]]:
        with self.lock:
            buffer = list(self.log_buffer)
        if count <= 0:
            return buffer
        return buffer[-count:]

    def update_stage(self, name: str, status: str, message: Optional[str] = None) -> None:
        now = _now_iso()
        with self.lock:
            for stage in self.stages:
                if stage.name == name:
                    stage.status = status
                    if status == "running" and not stage.started_at:
                        stage.started_at = now
                    if status in {"completed", "failed", "skipped", "blocked"}:
                        stage.finished_at = now
                    if message:
                        stage.message = message
                    self.updated_at = now
                    return
            stage = Stage(name=name, status=status, started_at=now if status == "running" else None)
            if status in {"completed", "failed", "skipped", "blocked"}:
                stage.finished_at = now
            if message:
                stage.message = message
            self.stages.append(stage)
            self.updated_at = now

    def to_dict(self, include_logs: bool = False, log_tail: int = DEFAULT_TAIL) -> Dict[str, Any]:
        with self.lock:
            data = {
                "id": self.job_id,
                "workflow": self.workflow,
                "project_name": self.project_name,
                "owner": self.owner,
                "status": self.status,
                "progress": self.progress,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "exit_code": self.exit_code,
                "restart_count": self.restart_count,
                "output_paths": self.output_paths,
                "metrics": dict(self.metrics),
                "stages": [stage.__dict__ for stage in self.stages],
                "pid": self.pid,
                "repo_info": self.repo_info,
            }
        if include_logs:
            data["logs"] = self.get_log_tail(log_tail)
        return data


class JobManager:
    def __init__(self, workers: int = DEFAULT_WORKERS):
        self.jobs: Dict[str, Job] = {}
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.workers: List[threading.Thread] = []
        _ensure_dirs()
        for idx in range(max(1, workers)):
            t = threading.Thread(target=self._worker_loop, args=(idx,), daemon=True)
            t.start()
            self.workers.append(t)

    def submit_job(self, payload: Dict[str, Any], owner: str) -> Job:
        job_id = uuid.uuid4().hex
        job_dir = os.path.join(JOB_ROOT, job_id)
        os.makedirs(job_dir, exist_ok=True)
        log_path = os.path.join(job_dir, "job.log")
        events_path = os.path.join(job_dir, "events.jsonl")
        job = Job(job_id=job_id, payload=payload, owner=owner, log_path=log_path, events_path=events_path)
        job.output_paths = self._resolve_output_paths(job)
        with self.lock:
            self.jobs[job_id] = job
        self.queue.put(job_id)
        return job

    def get_job(self, job_id: str, owner: Optional[str] = None) -> Optional[Job]:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            return None
        if owner and job.owner != owner:
            return None
        return job

    def list_jobs(self, status: Optional[str] = None, owner: Optional[str] = None) -> List[Job]:
        with self.lock:
            jobs = list(self.jobs.values())
        if owner:
            jobs = [job for job in jobs if job.owner == owner]
        if status:
            jobs = [job for job in jobs if job.status == status]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def pause_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if not job.process or job.status != "running":
            job.set_status("paused")
            return True
        if os.name != "nt":
            try:
                os.kill(job.process.pid, signal.SIGSTOP)
                job.set_status("paused")
                job.append_log("Paused by user")
                return True
            except Exception:
                return False
        return False

    def resume_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if not job.process:
            job.set_status("queued")
            self.queue.put(job_id)
            return True
        if os.name != "nt":
            try:
                os.kill(job.process.pid, signal.SIGCONT)
                job.set_status("running")
                job.append_log("Resumed by user")
                return True
            except Exception:
                return False
        return False

    def stop_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        job.stop_requested = True
        if job.process and job.process.poll() is None:
            try:
                job.process.terminate()
                job.append_log("Stop requested")
                return True
            except Exception:
                return False
        job.set_status("stopped")
        return True

    def restart_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if job.status in {"running", "paused"}:
            return False
        job.restart_count += 1
        job.stop_requested = False
        job.exit_code = None
        job.started_at = None
        job.finished_at = None
        job.progress = 0
        job.stages = []
        job.metrics = {
            "token_usage": {"prompt": 0, "completion": 0, "total": 0, "cached": None},
            "errors": 0,
            "resolved": 0,
            "warnings": 0,
            "queue_wait_sec": None,
            "runtime_sec": None,
        }
        job.append_log(f"--- restart #{job.restart_count} ---")
        job.set_status("queued")
        self.queue.put(job_id)
        return True

    def _worker_loop(self, worker_id: int) -> None:
        while True:
            job_id = self.queue.get()
            if job_id is None:
                self.queue.task_done()
                break
            job = self.get_job(job_id)
            if not job:
                self.queue.task_done()
                continue
            if job.status in {"stopped", "completed", "failed"}:
                self.queue.task_done()
                continue
            if job.status == "paused":
                self.queue.put(job_id)
                self.queue.task_done()
                time.sleep(0.5)
                continue
            self._run_job(job)
            self.queue.task_done()

    def _run_job(self, job: Job) -> None:
        job.set_status("running")
        job.started_at = _now_iso()
        job.metrics["queue_wait_sec"] = self._compute_wait_seconds(job)
        job.set_progress(5)
        try:
            self._prepare_repo(job)
            self._guardrail_check(job)
        except Exception as exc:
            job.append_log(f"Job blocked: {exc}")
            job.exit_code = 1
            job.set_status("failed")
            job.set_progress(100)
            job.finished_at = _now_iso()
            return
        try:
            command = self._build_command(job)
        except Exception as exc:
            job.append_log(f"Failed to build command: {exc}")
            job.exit_code = 1
            job.set_status("failed")
            job.set_progress(100)
            job.finished_at = _now_iso()
            return
        job.append_log("Starting job: " + " ".join(command))
        try:
            env = os.environ.copy()
            use_defaults = job.payload.get("use_default_secrets", True)
            if use_defaults and job.owner:
                env.update(_get_secret_store(job.owner).get_env())
            job_secrets = job.payload.get("job_secrets") or {}
            if isinstance(job_secrets, list):
                for entry in job_secrets:
                    if not isinstance(entry, dict):
                        continue
                    name = (entry.get("name") or "").strip()
                    value = (entry.get("value") or "").strip()
                    if name and value:
                        env[name] = value
            elif isinstance(job_secrets, dict):
                for name, value in job_secrets.items():
                    if name and value:
                        env[str(name)] = str(value)
            job.process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            job.pid = job.process.pid
        except Exception as exc:
            job.append_log(f"Failed to start process: {exc}")
            job.exit_code = 1
            job.set_status("failed")
            job.set_progress(100)
            job.finished_at = _now_iso()
            return

        assert job.process.stdout is not None
        for line in job.process.stdout:
            if not line:
                continue
            if self._handle_event_line(job, line):
                continue
            self._update_metrics(job, line)
            job.append_log(line)

        job.exit_code = job.process.wait()
        job.finished_at = _now_iso()
        job.metrics["runtime_sec"] = self._compute_runtime_seconds(job)
        if job.exit_code == 0:
            try:
                self._finalize_repo(job)
            except Exception as exc:
                job.append_log(f"Repo finalize failed: {exc}")
        if job.stop_requested:
            job.set_status("stopped")
        elif job.exit_code == 0:
            job.set_status("completed")
        else:
            job.set_status("failed")
        job.set_progress(100)
        job.append_log(f"Job finished with exit code {job.exit_code}")

    def _handle_event_line(self, job: Job, line: str) -> bool:
        if not line.startswith("__RAG_EVENT__ "):
            return False
        payload = line[len("__RAG_EVENT__ "):].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return False
        event_type = data.get("type")
        if event_type == "workflow_selected":
            workflow = data.get("workflow")
            if workflow:
                job.workflow = workflow
        elif event_type == "stage":
            stage_name = data.get("stage")
            status = data.get("status")
            progress = data.get("progress")
            message = data.get("message")
            if stage_name and status:
                job.update_stage(stage_name, status, message=message)
            if isinstance(progress, int):
                job.set_progress(progress)
        elif event_type == "workflow_complete":
            status = data.get("status")
            if status and job.status == "running":
                job.update_stage("finalize", status)
        return True

    def _update_metrics(self, job: Job, line: str) -> None:
        if "ERROR" in line or "Traceback" in line:
            job.metrics["errors"] += 1
        if "WARNING" in line:
            job.metrics["warnings"] += 1
        if re.search(r"\brecover(?:ed|y)\b|\bretry(?:ing|ed)\b|\bfallback\b", line, re.IGNORECASE):
            job.metrics["resolved"] += 1
        match = TOKEN_RE.search(line)
        if match:
            run_prompt = self._coerce_int(match.group("run_prompt"))
            run_completion = self._coerce_int(match.group("run_completion"))
            run_total = self._coerce_int(match.group("run_total"))
            cached = self._coerce_int(match.group("cached")) if match.group("cached") else None
            usage = job.metrics.get("token_usage") or {}
            usage["prompt"] = run_prompt or usage.get("prompt", 0)
            usage["completion"] = run_completion or usage.get("completion", 0)
            usage["total"] = run_total or usage.get("total", 0)
            if cached is not None:
                usage["cached"] = cached
            job.metrics["token_usage"] = usage

    def _build_command(self, job: Job) -> List[str]:
        payload = job.payload
        workflow = payload.get("workflow") or "project_solver"
        command = [
            os.getenv("REFINER_PYTHON", sys.executable),
            os.path.join(BASE_DIR, "run_refiner.py"),
            "--log-file",
            job.log_path,
            "--emit-events",
            "--events-file",
            job.events_path,
        ]
        if payload.get("verbose", True):
            command.append("--verbose")
        if payload.get("debug"):
            command.append("--debug")

        self._add_llm_args(command, payload)

        if workflow == "project_solver" or workflow == "project":
            project_root = self._resolve_project_root(job)
            command += ["--project", project_root]
            requirements_path = self._resolve_requirements(job)
            if requirements_path:
                command += ["--requirements", requirements_path]
            if payload.get("project_run"):
                command.append("--project-run")
            if payload.get("project_max_steps"):
                command += ["--project-max-steps", str(payload.get("project_max_steps"))]
            if payload.get("project_iterations"):
                command += ["--project-iterations", str(payload.get("project_iterations"))]
            if payload.get("project_output_dir"):
                command += ["--project-output-dir", str(payload.get("project_output_dir"))]
            if payload.get("codingagent"):
                command += ["--codingagent", payload.get("codingagent")]
            if payload.get("codingagent_fallback"):
                command += ["--codingagent-fallback", payload.get("codingagent_fallback")]
            if payload.get("codingagent_model"):
                command += ["--codingagent-model", payload.get("codingagent_model")]
            if payload.get("codingagent_reasoning_effort"):
                command += ["--codingagent-reasoning-effort", payload.get("codingagent_reasoning_effort")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "topic_research":
            source_path = self._resolve_topic_source(job)
            command += ["--topic-research", source_path]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
            if payload.get("max_iterations"):
                command += ["--max-iterations", str(payload.get("max_iterations"))]
            for ctx in payload.get("context_sources", []) or []:
                command += ["--context", ctx]
            if payload.get("references_output"):
                command += ["--references-output", payload.get("references_output")]
        elif workflow == "jira_analysis":
            command.append("--analyze-jira")
            if payload.get("projects"):
                command += ["--projects", payload.get("projects")]
            if payload.get("jql"):
                command += ["--jql", payload.get("jql")]
            if payload.get("action_plan"):
                command.append("--action-plan")
            if payload.get("dry_run"):
                command.append("--dry-run")
            if payload.get("post_comments"):
                command.append("--post-comments")
            if payload.get("post_target"):
                command += ["--post-target", payload.get("post_target")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "confluence_analysis":
            command.append("--analyze-confluence")
            if payload.get("space"):
                command += ["--space", payload.get("space")]
            if payload.get("use_rovo"):
                command.append("--use-rovo")
            if payload.get("action_plan"):
                command.append("--action-plan")
            if payload.get("dry_run"):
                command.append("--dry-run")
            if payload.get("post_comments"):
                command.append("--post-comments")
            if payload.get("post_target"):
                command += ["--post-target", payload.get("post_target")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "delivery_pipeline":
            command.append("--delivery")
            project_root = self._resolve_project_root(job)
            command += ["--project", project_root]
            if payload.get("delivery_config"):
                command += ["--delivery-config", payload.get("delivery_config")]
            if payload.get("delivery_run"):
                command.append("--delivery-run")
            if payload.get("delivery_allow_unfinished"):
                command.append("--delivery-allow-unfinished")
            if payload.get("delivery_enable_interim"):
                command.append("--delivery-enable-interim")
            if payload.get("delivery_project_solution"):
                command += ["--delivery-project-solution", payload.get("delivery_project_solution")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "jira_stats":
            pass

        if payload.get("disable_jira"):
            command.append("--disable-jira")
        if payload.get("disable_confluence"):
            command.append("--disable-confluence")

        extra_args = payload.get("extra_args")
        if isinstance(extra_args, str) and extra_args.strip():
            command += shlex.split(extra_args)
        return command

    def _resolve_output_paths(self, job: Job) -> Dict[str, str]:
        job_dir = os.path.join(JOB_ROOT, job.job_id)
        workflow = job.payload.get("workflow") or "project_solver"
        override = job.payload.get("output_path") or job.payload.get("output")
        if override:
            return {"primary": override}
        if workflow in {"project_solver", "project"}:
            return {"primary": os.path.join(job_dir, "project_solution.json")}
        if workflow == "topic_research":
            return {"primary": job.payload.get("topic_output") or os.path.join(job_dir, "researched_document.md")}
        if workflow == "jira_analysis":
            return {"primary": job.payload.get("jira_output") or os.path.join(job_dir, "jira_report.html")}
        if workflow == "confluence_analysis":
            return {"primary": job.payload.get("confluence_output") or os.path.join(job_dir, "confluence_report.html")}
        if workflow == "delivery_pipeline":
            return {"primary": job.payload.get("pipeline_output") or os.path.join(job_dir, "pipeline_report.json")}
        return {}

    def _resolve_project_root(self, job: Job) -> str:
        project_root = job.payload.get("project_root") or job.payload.get("delivery_project_root")
        project_name = job.payload.get("project_name")
        create_project = bool(job.payload.get("create_project"))
        if project_root:
            if create_project and not os.path.exists(project_root):
                os.makedirs(project_root, exist_ok=True)
            return project_root
        if project_name:
            slug = _slugify(project_name)
            project_root = os.path.join(PROJECTS_ROOT, f"{slug}-{job.job_id[:8]}")
            os.makedirs(project_root, exist_ok=True)
            return project_root
        raise ValueError("Project root or project name is required for this workflow")

    def _prepare_repo(self, job: Job) -> None:
        payload = job.payload
        repo_input = (payload.get("repo_url") or payload.get("repo") or "").strip()
        if not repo_input:
            if self._maybe_create_starter_repo(job):
                return
            return
        workflow = payload.get("workflow") or "project_solver"
        if workflow not in {"project_solver", "project", "delivery_pipeline"}:
            return
        owner, repo = self._parse_repo_input(repo_input)
        if not owner or not repo:
            raise ValueError("Invalid GitHub repo input. Use owner/repo or full URL.")
        fork_org = (payload.get("fork_org") or DEFAULT_FORK_ORG).strip()
        token = self._get_github_token(job)
        if not token:
            raise ValueError("Missing GitHub token. Add GITHUB_TOKEN in Credentials Vault or per-job secrets.")

        fork_repo = self._build_fork_repo_name(repo, job.owner or "user", job.project_name or repo)
        job.append_log(f"Preparing GitHub workspace for {owner}/{repo} -> {fork_org}/{fork_repo}")
        fork = self._ensure_fork(owner, repo, fork_org, fork_repo, token, job)
        default_branch = fork.get("default_branch") or payload.get("repo_branch") or "main"
        workspace = os.path.join(WORKSPACE_ROOT, job.job_id, repo)
        if os.path.exists(workspace):
            try:
                shutil.rmtree(workspace)
            except Exception:
                pass
        os.makedirs(os.path.dirname(workspace), exist_ok=True)

        fork_name = fork.get("name") or fork_repo
        clone_url = f"https://github.com/{fork_org}/{fork_name}.git"
        self._git_clone(clone_url, workspace, default_branch, token, job)
        branch_name = (payload.get("work_branch") or f"refiner/{job.job_id[:8]}").strip()
        self._git_checkout(workspace, branch_name, job)

        project_subdir = (payload.get("repo_subdir") or "").strip()
        project_root = os.path.join(workspace, project_subdir) if project_subdir else workspace
        if not os.path.isdir(project_root):
            raise ValueError(f"Project subdir does not exist: {project_subdir or '.'}")

        requirements_rel = (payload.get("requirements_relpath") or "").strip()
        if requirements_rel:
            req_path = os.path.join(workspace, requirements_rel)
            if not os.path.exists(req_path):
                raise ValueError(f"Requirements path not found: {requirements_rel}")
            payload["requirements_path"] = req_path
        elif payload.get("requirements_text"):
            req_path = os.path.join(workspace, "requirements.md")
            with open(req_path, "w", encoding="utf-8") as handle:
                handle.write(payload.get("requirements_text"))
            payload["requirements_path"] = req_path

        payload["project_root"] = project_root
        job.repo_info = {
            "source": "github",
            "owner": owner,
            "repo": repo,
            "fork_org": fork_org,
            "fork_repo": fork_name,
            "branch": branch_name,
            "workspace": workspace,
            "clone_url": clone_url,
        }
        if not job.project_name:
            job.project_name = repo

    def _guardrail_check(self, job: Job) -> None:
        payload = job.payload
        text = (payload.get("requirements_text") or "").strip()
        if text:
            reason = _guardrail_scan(text)
            if reason:
                raise ValueError(f"Guardrail blocked requirements: {reason}")
            return
        req_path = (payload.get("requirements_path") or "").strip()
        if req_path and os.path.exists(req_path):
            content = _read_file_limited(req_path, REQUIREMENTS_MAX_BYTES)
            reason = _guardrail_scan(content)
            if reason:
                raise ValueError(f"Guardrail blocked requirements file: {reason}")

    def _maybe_create_starter_repo(self, job: Job) -> bool:
        payload = job.payload
        workflow = payload.get("workflow") or "project_solver"
        if workflow not in {"project_solver", "project"}:
            return False
        if payload.get("project_root"):
            return False
        requirements_text = (payload.get("requirements_text") or "").strip()
        requirements_path = (payload.get("requirements_path") or "").strip()
        if not requirements_text and not requirements_path:
            return False
        project_name = job.project_name or payload.get("project_name") or ""
        if not project_name:
            raise ValueError("Project name is required to create a starter repo.")

        fork_org = (payload.get("fork_org") or DEFAULT_FORK_ORG).strip()
        token = self._get_github_token(job)
        if not token:
            raise ValueError("Missing GitHub token. Add GITHUB_TOKEN in Credentials Vault or per-job secrets.")

        repo_name = self._build_fork_repo_name(_slugify(project_name), job.owner or "user", project_name)
        job.append_log(f"Creating starter repo {fork_org}/{repo_name}")
        repo_data = self._ensure_repo_exists(fork_org, repo_name, token, job, payload)
        default_branch = repo_data.get("default_branch") or "main"
        workspace = os.path.join(WORKSPACE_ROOT, job.job_id, repo_name)
        if os.path.exists(workspace):
            try:
                shutil.rmtree(workspace)
            except Exception:
                pass
        os.makedirs(os.path.dirname(workspace), exist_ok=True)

        clone_url = f"https://github.com/{fork_org}/{repo_name}.git"
        self._git_clone(clone_url, workspace, default_branch, token, job)
        branch_name = (payload.get("work_branch") or f"refiner/{job.job_id[:8]}").strip()
        self._git_checkout(workspace, branch_name, job)

        requirements_rel = (payload.get("requirements_relpath") or "requirements.md").strip()
        req_path = os.path.join(workspace, requirements_rel)
        os.makedirs(os.path.dirname(req_path), exist_ok=True)
        if requirements_text:
            with open(req_path, "w", encoding="utf-8") as handle:
                handle.write(requirements_text)
        else:
            if not os.path.exists(requirements_path):
                raise ValueError("Requirements path not found on server.")
            shutil.copyfile(requirements_path, req_path)
        payload["requirements_path"] = req_path
        payload["project_root"] = workspace

        job.repo_info = {
            "source": "starter",
            "owner": fork_org,
            "repo": repo_name,
            "fork_org": fork_org,
            "fork_repo": repo_name,
            "branch": branch_name,
            "workspace": workspace,
            "clone_url": clone_url,
        }
        if not job.project_name:
            job.project_name = project_name
        return True

    def _finalize_repo(self, job: Job) -> None:
        if not job.repo_info:
            return
        workspace = job.repo_info.get("workspace")
        branch = job.repo_info.get("branch")
        if not workspace or not branch:
            return
        if not self._git_has_changes(workspace, job):
            job.append_log("No git changes detected; skipping commit/push.")
            return
        author_name = job.payload.get("git_author_name") or "refiner-bot"
        author_email = job.payload.get("git_author_email") or "automation@neuralmimicry.ai"
        commit_message = job.payload.get("commit_message") or f"Refiner updates ({job.job_id[:8]})"
        self._git_config(workspace, author_name, author_email, job)
        self._git_add_all(workspace, job)
        self._git_commit(workspace, commit_message, job)
        token = self._get_github_token(job)
        self._git_push(workspace, branch, token, job)
        fork_org = job.repo_info.get("fork_org")
        fork_repo = job.repo_info.get("fork_repo") or job.repo_info.get("repo")
        if fork_org and fork_repo:
            repo_url = f"https://github.com/{fork_org}/{fork_repo}"
            job.append_log(f"Repo ready: {repo_url} (branch: {branch})")
            job.repo_info["repo_url"] = repo_url

    def _get_github_token(self, job: Job) -> Optional[str]:
        job_secrets = job.payload.get("job_secrets") or []
        candidates = ["GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"]
        if isinstance(job_secrets, list):
            for entry in job_secrets:
                if not isinstance(entry, dict):
                    continue
                name = (entry.get("name") or "").strip()
                if name in candidates:
                    return (entry.get("value") or "").strip()
        elif isinstance(job_secrets, dict):
            for key in candidates:
                if key in job_secrets:
                    return str(job_secrets.get(key) or "").strip()
        if job.owner:
            env = _get_secret_store(job.owner).get_env()
            for key in candidates:
                if env.get(key):
                    return env.get(key)
        return None

    @staticmethod
    def _parse_repo_input(value: str) -> Optional[tuple]:
        value = value.strip()
        if value.startswith("http://") or value.startswith("https://"):
            match = re.match(r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\\.git)?$", value)
            if not match:
                return None, None
            return match.group("owner"), match.group("repo")
        if value.count("/") == 1:
            owner, repo = value.split("/", 1)
            repo = repo.strip()
            if repo.endswith(".git"):
                repo = repo[:-4]
            return owner.strip(), repo
        return None, None

    def _ensure_fork(
        self,
        owner: str,
        repo: str,
        fork_org: str,
        fork_repo: str,
        token: str,
        job: Job,
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        fork_url = f"https://api.github.com/repos/{fork_org}/{fork_repo}"
        parent_full_name = f"{owner}/{repo}"
        resp = requests.get(fork_url, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            parent = data.get("parent") or {}
            if parent.get("full_name") and parent.get("full_name") != parent_full_name:
                raise ValueError(f"Existing fork {fork_org}/{fork_repo} is not based on {parent_full_name}.")
            return data
        create_url = f"https://api.github.com/repos/{owner}/{repo}/forks"
        payload = {"organization": fork_org}
        create_resp = requests.post(create_url, headers=headers, json=payload, timeout=20)
        if create_resp.status_code not in (202, 201):
            raise ValueError(f"Fork failed: {create_resp.status_code} {create_resp.text}")
        for _ in range(20):
            time.sleep(2)
            resp = requests.get(fork_url, headers=headers, timeout=20)
            if resp.status_code == 200:
                return resp.json()
        # Fork may exist under default name; attempt rename if needed
        default_fork_url = f"https://api.github.com/repos/{fork_org}/{repo}"
        resp = requests.get(default_fork_url, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            parent = data.get("parent") or {}
            if parent.get("full_name") != parent_full_name:
                raise ValueError(f"Existing fork {fork_org}/{repo} is not based on {parent_full_name}.")
            if fork_repo != repo:
                rename_url = f"https://api.github.com/repos/{fork_org}/{repo}"
                rename_resp = requests.patch(rename_url, headers=headers, json={"name": fork_repo}, timeout=20)
                if rename_resp.status_code not in (200, 201):
                    raise ValueError(f"Fork rename failed: {rename_resp.status_code} {rename_resp.text}")
                return rename_resp.json()
            return data
        raise ValueError("Fork not ready yet. Try again shortly.")

    def _ensure_repo_exists(
        self,
        org: str,
        repo_name: str,
        token: str,
        job: Job,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        repo_url = f"https://api.github.com/repos/{org}/{repo_name}"
        resp = requests.get(repo_url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json()
        private_flag = payload.get("repo_private")
        if private_flag is None:
            private_flag = DEFAULT_REPO_PRIVATE
        create_url = f"https://api.github.com/orgs/{org}/repos"
        body = {
            "name": repo_name,
            "private": bool(private_flag),
            "auto_init": True,
            "description": payload.get("repo_description") or "Refiner starter repo",
        }
        create_resp = requests.post(create_url, headers=headers, json=body, timeout=20)
        if create_resp.status_code not in (201, 202):
            raise ValueError(f"Repo create failed: {create_resp.status_code} {create_resp.text}")
        return create_resp.json()

    @staticmethod
    def _build_fork_repo_name(repo: str, owner: str, project_name: str) -> str:
        repo_slug = _slugify(repo)
        owner_slug = _slugify(owner)
        project_slug = _slugify(project_name)
        if project_slug and project_slug != repo_slug:
            name = f"{repo_slug}-{owner_slug}-{project_slug}"
        else:
            name = f"{repo_slug}-{owner_slug}"
        if len(name) > 90:
            trim = name[:90]
            return trim.rstrip("-")
        return name

    def _git_env(self, token: Optional[str], askpass_dir: str) -> Dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if token:
            os.makedirs(askpass_dir, exist_ok=True)
            askpass_path = os.path.join(askpass_dir, ".git-askpass.sh")
            script = (
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "*Username*) echo \"x-access-token\" ;;\n"
                "*Password*) echo \"" + token.replace('"', '\\"') + "\" ;;\n"
                "*) echo \"" + token.replace('"', '\\"') + "\" ;;\n"
                "esac\n"
            )
            with open(askpass_path, "w", encoding="utf-8") as handle:
                handle.write(script)
            try:
                os.chmod(askpass_path, 0o700)
            except Exception:
                pass
            env["GIT_ASKPASS"] = askpass_path
        return env

    def _git_run(self, command: List[str], cwd: str, job: Job, token: Optional[str] = None) -> subprocess.CompletedProcess:
        askpass_dir = os.path.dirname(job.log_path) if job.log_path else cwd
        env = self._git_env(token, askpass_dir)
        job.append_log(f"git: {' '.join(command)}")
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=env, check=False)

    def _git_clone(self, clone_url: str, workspace: str, branch: str, token: str, job: Job) -> None:
        result = self._git_run(
            ["git", "clone", "--depth", "1", "--branch", branch, clone_url, workspace],
            cwd=BASE_DIR,
            job=job,
            token=token,
        )
        if result.returncode != 0:
            raise ValueError(f"git clone failed: {result.stderr.strip()}")

    def _git_checkout(self, workspace: str, branch: str, job: Job) -> None:
        result = self._git_run(["git", "checkout", "-B", branch], cwd=workspace, job=job)
        if result.returncode != 0:
            raise ValueError(f"git checkout failed: {result.stderr.strip()}")

    def _git_has_changes(self, workspace: str, job: Job) -> bool:
        result = self._git_run(["git", "status", "--porcelain"], cwd=workspace, job=job)
        return bool(result.stdout.strip())

    def _git_config(self, workspace: str, name: str, email: str, job: Job) -> None:
        self._git_run(["git", "config", "user.name", name], cwd=workspace, job=job)
        self._git_run(["git", "config", "user.email", email], cwd=workspace, job=job)

    def _git_add_all(self, workspace: str, job: Job) -> None:
        result = self._git_run(["git", "add", "-A"], cwd=workspace, job=job)
        if result.returncode != 0:
            raise ValueError(f"git add failed: {result.stderr.strip()}")

    def _git_commit(self, workspace: str, message: str, job: Job) -> None:
        result = self._git_run(["git", "commit", "-m", message], cwd=workspace, job=job)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "nothing to commit" in stderr.lower():
                return
            raise ValueError(f"git commit failed: {stderr}")

    def _git_push(self, workspace: str, branch: str, token: Optional[str], job: Job) -> None:
        result = self._git_run(["git", "push", "origin", branch], cwd=workspace, job=job, token=token)
        if result.returncode != 0:
            raise ValueError(f"git push failed: {result.stderr.strip()}")

    def _resolve_requirements(self, job: Job) -> Optional[str]:
        requirements_path = job.payload.get("requirements_path")
        requirements_text = job.payload.get("requirements_text")
        if requirements_path:
            return requirements_path
        if requirements_text:
            job_dir = os.path.join(JOB_ROOT, job.job_id)
            path = os.path.join(job_dir, "requirements.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(requirements_text)
            return path
        return None

    def _resolve_topic_source(self, job: Job) -> str:
        source = job.payload.get("topic_source") or job.payload.get("topic_research")
        if not source:
            raise ValueError("Topic source is required for topic research")
        if source.startswith("http://") or source.startswith("https://"):
            return source
        job_dir = os.path.join(JOB_ROOT, job.job_id)
        path = os.path.join(job_dir, "topic.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        return path

    def _add_llm_args(self, command: List[str], payload: Dict[str, Any]) -> None:
        if payload.get("llm_provider"):
            command += ["--llm-provider", payload.get("llm_provider")]
        if payload.get("llm_model"):
            command += ["--llm-model", payload.get("llm_model")]
        if payload.get("llm_reasoning_effort"):
            command += ["--llm-reasoning-effort", payload.get("llm_reasoning_effort")]
        if payload.get("fallback_llm_provider"):
            command += ["--fallback-llm-provider", payload.get("fallback_llm_provider")]
        if payload.get("fallback_llm_model"):
            command += ["--fallback-llm-model", payload.get("fallback_llm_model")]
        if payload.get("ollama_base_url"):
            command += ["--ollama-base-url", payload.get("ollama_base_url")]
        if payload.get("llm_max_tokens"):
            command += ["--llm-max-tokens", str(payload.get("llm_max_tokens"))]
        if payload.get("llm_chunk_size"):
            command += ["--llm-chunk-size", str(payload.get("llm_chunk_size"))]
        if payload.get("llm_temperature") is not None:
            command += ["--llm-temperature", str(payload.get("llm_temperature"))]
        if payload.get("llm_timeout"):
            command += ["--llm-timeout", str(payload.get("llm_timeout"))]
        if payload.get("llm_inter_request_gap"):
            command += ["--llm-inter-request-gap", str(payload.get("llm_inter_request_gap"))]

    def _compute_wait_seconds(self, job: Job) -> Optional[float]:
        if not job.started_at:
            return None
        try:
            created = time.strptime(job.created_at, "%Y-%m-%dT%H:%M:%SZ")
            started = time.strptime(job.started_at, "%Y-%m-%dT%H:%M:%SZ")
            return time.mktime(started) - time.mktime(created)
        except Exception:
            return None

    def _compute_runtime_seconds(self, job: Job) -> Optional[float]:
        if not (job.started_at and job.finished_at):
            return None
        try:
            started = time.strptime(job.started_at, "%Y-%m-%dT%H:%M:%SZ")
            finished = time.strptime(job.finished_at, "%Y-%m-%dT%H:%M:%SZ")
            return time.mktime(finished) - time.mktime(started)
        except Exception:
            return None

    @staticmethod
    def _coerce_int(value: Optional[str]) -> Optional[int]:
        if value is None or value == "?":
            return None
        try:
            return int(value)
        except Exception:
            return None


manager = JobManager()
user_store = UserStore(USERS_PATH)
user_store.ensure_admin_from_env()
_secret_stores: Dict[str, SecretStore] = {}


def _get_secret_store(user: str) -> SecretStore:
    key = user or "default"
    store = _secret_stores.get(key)
    if store:
        return store
    path = os.path.join(SECRET_STORE_ROOT, f"{key}.json")
    store = SecretStore(path)
    _secret_stores[key] = store
    return store


def _get_github_api_token(user: Optional[str]) -> Optional[str]:
    if not user:
        return None
    env = _get_secret_store(user).get_env()
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        if env.get(key):
            return env.get(key)
    return None


def _load_llm_config() -> Dict[str, Any]:
    path = os.path.join(BASE_DIR, "config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_llm_settings(
    user: str,
    provider_hint: Optional[str] = None,
    model_hint: Optional[str] = None,
) -> Dict[str, Any]:
    env = _get_secret_store(user).get_env()
    cfg = _load_llm_config()
    providers = cfg.get("llm_providers") if isinstance(cfg.get("llm_providers"), list) else []

    provider_type = None
    model = model_hint
    base_url = None

    if provider_hint:
        match = next((p for p in providers if p.get("name") == provider_hint), None)
        if match:
            provider_type = match.get("type") or provider_hint
            model = model or match.get("model")
            base_url = match.get("base_url")
        else:
            provider_type = provider_hint

    if not provider_type:
        if env.get("OPENAI_API_KEY"):
            provider_type = "openai"
        elif env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or env.get("GOOGLE_GENERATIVE_AI_API_KEY"):
            provider_type = "gemini"
        else:
            provider_type = "ollama"

    if provider_type in {"gpt", "chatgpt", "openai"}:
        api_key = env.get("OPENAI_API_KEY")
    elif provider_type in {"gemini", "google"}:
        api_key = (
            env.get("GEMINI_API_KEY")
            or env.get("GOOGLE_API_KEY")
            or env.get("GOOGLE_GENERATIVE_AI_API_KEY")
        )
    else:
        api_key = None

    if not base_url:
        base_url = env.get("OLLAMA_BASE_URL") if provider_type == "ollama" else None

    return {
        "provider": provider_type,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def _guardrail_scan(text: str) -> Optional[str]:
    if not text:
        return None
    lower = text.lower()
    for category, patterns in GUARDRAIL_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, lower, flags=re.IGNORECASE | re.DOTALL):
                return f"Detected {category} content via pattern: {pat}"
    return None


def _read_file_limited(path: str, max_bytes: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(max_bytes)
    except Exception:
        return ""


def _extract_json_payload(text: str) -> Optional[Any]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    start = None
    end = None
    for i, ch in enumerate(cleaned):
        if ch in "{[":
            start = i
            break
    for i in range(len(cleaned) - 1, -1, -1):
        if cleaned[i] in "]}":
            end = i + 1
            break
    if start is not None and end is not None and end > start:
        try:
            return json.loads(cleaned[start:end])
        except Exception:
            return None
    return None


def _current_user() -> Optional[str]:
    return session.get("user")


def _is_secure_request() -> bool:
    if request.is_secure:
        return True
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto:
        proto = forwarded_proto.split(",")[0].strip().lower()
        if proto == "https":
            return True
    return False


def _enforce_https() -> Optional[Response]:
    if not ENFORCE_HTTPS:
        return None
    if _is_secure_request():
        return None
    if request.method == "GET":
        https_url = request.url.replace("http://", "https://", 1)
        return redirect(https_url, code=308)
    return jsonify({"error": "https_required"}), 403


def _allowed_origin() -> Optional[str]:
    origin = request.headers.get("Origin")
    if not origin:
        return None
    normalized = origin.rstrip("/")
    if normalized in CORS_ORIGINS:
        return normalized
    return None


def _add_vary(response: Response, value: str) -> None:
    current = response.headers.get("Vary")
    if not current:
        response.headers["Vary"] = value
        return
    existing = [item.strip() for item in current.split(",") if item.strip()]
    if value not in existing:
        response.headers["Vary"] = ", ".join(existing + [value])


def _apply_cors(response: Response) -> Response:
    origin = _allowed_origin()
    if not origin:
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = ", ".join(CORS_ALLOW_HEADERS)
    response.headers["Access-Control-Allow-Methods"] = ", ".join(CORS_ALLOW_METHODS)
    response.headers["Access-Control-Max-Age"] = str(CORS_MAX_AGE)
    _add_vary(response, "Origin")
    _add_vary(response, "Access-Control-Request-Method")
    _add_vary(response, "Access-Control-Request-Headers")
    if ENFORCE_HTTPS and _is_secure_request():
        response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    return response


def _metrics_path_label() -> str:
    if request.url_rule and request.url_rule.rule:
        return request.url_rule.rule
    path = request.path or ""
    if path.startswith("/static/"):
        return "/static/*"
    if path.startswith("/public/"):
        return "/public/*"
    return path or "unknown"


def _require_login() -> Optional[Response]:
    path = request.path or ""
    if path.startswith("/static/") or path.startswith("/favicon"):
        return None
    if path == METRICS_PATH:
        return None
    if path in {
        "/login",
        "/setup",
        "/api/login",
        "/api/logout",
        "/api/session",
        "/api/setup",
    } or path.startswith("/api/health"):
        return None
    is_api = path.startswith("/api/")
    if not user_store.has_users():
        if is_api:
            return jsonify({"error": "unauthorized"}), 401
        if path != "/setup":
            return redirect(url_for("setup"))
        return None
    if not _current_user():
        if is_api:
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login"))
    return None


@app.before_request
def _before_request() -> Optional[Response]:
    if METRICS_ENABLED:
        g.metrics_start = time.time()
        INFLIGHT.inc()
    https_block = _enforce_https()
    if https_block:
        return https_block
    if request.method == "OPTIONS":
        return Response(status=204)
    return _require_login()


@app.after_request
def _after_request(response: Response) -> Response:
    if METRICS_ENABLED:
        try:
            path_label = _metrics_path_label()
            elapsed = time.time() - getattr(g, "metrics_start", time.time())
            REQUEST_LATENCY.labels(request.method, path_label).observe(elapsed)
            REQUEST_COUNT.labels(request.method, path_label, response.status_code).inc()
        finally:
            INFLIGHT.dec()
    return _apply_cors(response)


@app.route("/")
def index() -> str:
    return render_template("index.html", current_user=_current_user(), api_base=API_BASE)


@app.route("/public/<path:filename>")
def public_asset(filename: str) -> Response:
    return send_from_directory(PUBLIC_DIR, filename)


@app.route("/favicon.ico")
def favicon() -> Response:
    return send_from_directory(PUBLIC_DIR, "favicon.ico")


@app.route(METRICS_PATH)
def metrics() -> Response:
    if not METRICS_ENABLED:
        return jsonify({"error": "metrics_disabled"}), 404

    try:
        WORKER_COUNT.set(len(manager.workers))
        JOB_QUEUE_DEPTH.set(manager.queue.qsize())
        UPTIME.set(time.time() - APP_START_TIME)

        status_counts: Dict[str, int] = {}
        with manager.lock:
            jobs_snapshot = list(manager.jobs.values())
        for job in jobs_snapshot:
            status_counts[job.status] = status_counts.get(job.status, 0) + 1

        for status in ("queued", "running", "paused", "stopped", "completed", "failed"):
            JOBS_BY_STATUS.labels(status=status).set(status_counts.get(status, 0))
    except Exception:
        pass

    payload = generate_latest()
    return Response(payload, mimetype=CONTENT_TYPE_LATEST)


@app.route("/login", methods=["GET", "POST"])
def login() -> Response:
    if not user_store.has_users():
        return redirect(url_for("setup"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if user_store.verify(username, password):
            session["user"] = username
            return redirect(url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error, api_base=API_BASE)


@app.route("/logout")
def logout() -> Response:
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/api/login", methods=["POST"])
def api_login() -> Response:
    if not user_store.has_users():
        return jsonify({"error": "setup_required"}), 400
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict):
        payload = {}
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "username_and_password_required"}), 400
    if not user_store.verify(username, password):
        return jsonify({"error": "invalid_credentials"}), 401
    session["user"] = username
    return jsonify({"status": "ok", "user": username, "role": user_store.get_role(username)})


@app.route("/api/setup", methods=["POST"])
def api_setup() -> Response:
    if user_store.has_users():
        return jsonify({"error": "setup_not_allowed"}), 409
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict):
        payload = {}
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    confirm = (payload.get("confirm") or "").strip()
    if not username or not password:
        return jsonify({"error": "username_and_password_required"}), 400
    if not USERNAME_RE.match(username):
        return jsonify(
            {
                "error": "invalid_username",
                "details": "Username must be 3-32 chars (letters, numbers, underscore, dash).",
            }
        ), 400
    if len(password) < 8:
        return jsonify({"error": "password_too_short", "details": "Password must be at least 8 characters."}), 400
    if confirm and password != confirm:
        return jsonify({"error": "password_mismatch", "details": "Passwords do not match."}), 400
    user_store.create_user(username, password, role="admin")
    session["user"] = username
    return jsonify({"status": "ok", "user": username, "role": user_store.get_role(username)}), 201


@app.route("/api/logout", methods=["POST"])
def api_logout() -> Response:
    session.pop("user", None)
    return jsonify({"status": "ok"})


@app.route("/api/session")
def api_session() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"authenticated": False, "user": None}), 200
    return jsonify({"authenticated": True, "user": user, "role": user_store.get_role(user)})


@app.route("/setup", methods=["GET", "POST"])
def setup() -> Response:
    if user_store.has_users():
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        confirm = (request.form.get("confirm") or "").strip()
        if not USERNAME_RE.match(username):
            error = "Username must be 3-32 chars (letters, numbers, underscore, dash)."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            user_store.create_user(username, password, role="admin")
            session["user"] = username
            return redirect(url_for("index"))
    return render_template("setup.html", error=error, api_base=API_BASE)


@app.route("/api/health")
def health() -> Response:
    return jsonify({"status": "ok", "jobs": len(manager.jobs), "workers": len(manager.workers)})


@app.route("/api/jobs", methods=["GET", "POST"])
def jobs() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "POST":
        payload = request.get_json(force=True, silent=True) or {}
        payload["owner"] = user
        job = manager.submit_job(payload, owner=user)
        return jsonify(job.to_dict())
    status = request.args.get("status")
    jobs_list = [job.to_dict() for job in manager.list_jobs(status=status, owner=user)]
    return jsonify({"jobs": jobs_list})


@app.route("/api/jobs/<job_id>")
def job_detail(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job.to_dict(include_logs=True, log_tail=DEFAULT_TAIL))


@app.route("/api/jobs/<job_id>/logs")
def job_logs(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    tail = request.args.get("tail")
    try:
        tail_count = int(tail) if tail else DEFAULT_TAIL
    except Exception:
        tail_count = DEFAULT_TAIL
    return jsonify({"logs": job.get_log_tail(tail_count)})


@app.route("/api/jobs/<job_id>/logs/stream")
def job_logs_stream(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404

    def generate():
        q = job.add_listener()
        try:
            while True:
                try:
                    entry = q.get(timeout=1.0)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(entry)
        finally:
            job.remove_listener(q)

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.route("/api/jobs/<job_id>/actions", methods=["POST"])
def job_actions(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    action = payload.get("action")
    success = False
    if action == "pause":
        success = manager.pause_job(job_id)
    elif action == "resume":
        success = manager.resume_job(job_id)
    elif action == "stop":
        success = manager.stop_job(job_id)
    elif action == "restart":
        success = manager.restart_job(job_id)
    else:
        return jsonify({"error": "unknown action"}), 400
    if not success:
        return jsonify({"error": "action failed"}), 409
    return jsonify(job.to_dict())


@app.route("/api/secrets", methods=["GET", "POST"])
def secrets() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    store = _get_secret_store(user)
    if request.method == "GET":
        return jsonify({"secrets": store.list_masked()})
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name") or "").strip()
    value = str(payload.get("value") or "").strip()
    if not name or not value:
        return jsonify({"error": "name and value are required"}), 400
    if not SECRET_NAME_RE.match(name):
        return jsonify({"error": "invalid secret name"}), 400
    store.set(name, value)
    return jsonify({"name": name, "masked": SecretStore._mask(value), "updated_at": _now_iso()})


@app.route("/api/secrets/<name>", methods=["DELETE"])
def delete_secret(name: str) -> Response:
    if not SECRET_NAME_RE.match(name):
        return jsonify({"error": "invalid secret name"}), 400
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    store = _get_secret_store(user)
    deleted = store.delete(name)
    if not deleted:
        return jsonify({"error": "secret not found"}), 404
    return jsonify({"status": "deleted"})


@app.route("/api/github/tree", methods=["POST"])
def github_tree() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    repo_input = str(payload.get("repo_url") or payload.get("repo") or "").strip()
    branch = str(payload.get("branch") or "").strip()
    if not repo_input:
        return jsonify({"error": "repo is required"}), 400
    owner, repo = JobManager._parse_repo_input(repo_input)
    if not owner or not repo:
        return jsonify({"error": "invalid repo input"}), 400
    token = _get_github_api_token(user)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    repo_url = f"https://api.github.com/repos/{owner}/{repo}"
    repo_resp = requests.get(repo_url, headers=headers, timeout=20)
    if repo_resp.status_code != 200:
        return jsonify({"error": "repo lookup failed", "details": repo_resp.text}), 400
    repo_data = repo_resp.json()
    default_branch = repo_data.get("default_branch") or "main"
    branch = branch or default_branch

    tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}"
    tree_resp = requests.get(tree_url, headers=headers, params={"recursive": "1"}, timeout=30)
    if tree_resp.status_code != 200:
        return jsonify({"error": "tree lookup failed", "details": tree_resp.text}), 400
    tree_data = tree_resp.json()
    items = tree_data.get("tree") or []
    max_entries = int(payload.get("max_entries") or 4000)
    trimmed = items[:max_entries]
    response_items = [
        {
            "path": item.get("path"),
            "type": item.get("type"),
            "size": item.get("size"),
        }
        for item in trimmed
        if item.get("path") and item.get("type") in {"blob", "tree"}
    ]
    return jsonify({"items": response_items, "branch": branch, "owner": owner, "repo": repo})


@app.route("/api/assistant/requirements", methods=["POST"])
def assistant_requirements() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    mode = (payload.get("mode") or "ask").strip().lower()
    prompt = (payload.get("prompt") or "").strip()
    requirements_text = (payload.get("requirements_text") or "").strip()
    messages = payload.get("messages") or []

    if mode not in {"ask", "draft"}:
        return jsonify({"error": "invalid mode"}), 400

    guard_text = "\n".join([requirements_text, prompt]).strip()
    if guard_text:
        reason = _guardrail_scan(guard_text)
        if reason:
            return jsonify({"error": "guardrail_blocked", "details": reason}), 400

    settings = _resolve_llm_settings(
        user=user,
        provider_hint=payload.get("provider") or payload.get("llm_provider"),
        model_hint=payload.get("model") or payload.get("llm_model"),
    )
    try:
        provider = get_provider(
            settings["provider"],
            model=settings.get("model"),
            base_url=settings.get("base_url"),
            api_key=settings.get("api_key"),
            inter_request_gap=0.0,
        )
    except LLMError as exc:
        return jsonify({"error": "llm_unavailable", "details": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": "llm_init_failed", "details": str(exc)}), 400

    if not provider:
        return jsonify({"error": "llm_unavailable"}), 400

    system = (
        "You are a requirements assistant. Help the user craft clear, testable requirements. "
        "Ask concise clarifying questions when needed. "
        "When drafting, output structured Markdown with sections: Overview, Goals, Non-Goals, "
        "Functional Requirements, Non-Functional Requirements, Acceptance Criteria, Risks."
    )

    chat_messages: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            chat_messages.append({"role": role, "content": content})

    if mode == "draft":
        user_text = "Draft a complete requirements document."
        if requirements_text:
            user_text += f"\n\nCurrent notes:\n{requirements_text}"
        chat_messages.append({"role": "user", "content": user_text})
    else:
        if not prompt:
            return jsonify({"error": "prompt_required"}), 400
        if requirements_text:
            prompt = f"Current requirements notes:\\n{requirements_text}\\n\\nUser question: {prompt}"
        chat_messages.append({"role": "user", "content": prompt})

    temperature = payload.get("temperature", 0.2)
    max_tokens = payload.get("max_tokens")
    reasoning_effort = payload.get("reasoning_effort")
    try:
        response = provider.predict(
            messages=chat_messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        return jsonify({"error": "llm_request_failed", "details": str(exc)}), 400

    return jsonify(
        {
            "reply": response.text,
            "provider": response.provider or settings.get("provider"),
            "model": response.model or settings.get("model"),
        }
    )


@app.route("/api/assistant/form-fill", methods=["POST"])
def assistant_form_fill() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    fields = payload.get("fields") or []
    prompt = (payload.get("prompt") or "").strip()
    workflow = (payload.get("workflow") or "").strip()
    scope = (payload.get("scope") or "").strip()
    if not isinstance(fields, list) or not fields:
        return jsonify({"error": "fields_required"}), 400

    reason = _guardrail_scan(prompt)
    if reason:
        return jsonify({"error": "guardrail_blocked", "details": reason}), 400

    settings = _resolve_llm_settings(
        user=user,
        provider_hint=payload.get("provider") or payload.get("llm_provider"),
        model_hint=payload.get("model") or payload.get("llm_model"),
    )
    try:
        provider = get_provider(
            settings["provider"],
            model=settings.get("model"),
            base_url=settings.get("base_url"),
            api_key=settings.get("api_key"),
            inter_request_gap=0.0,
        )
    except Exception as exc:
        return jsonify({"error": "llm_init_failed", "details": str(exc)}), 400
    if not provider:
        return jsonify({"error": "llm_unavailable"}), 400

    field_descriptions = []
    allowed_ids = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        field_id = field.get("id") or field.get("field_id")
        if not field_id:
            continue
        allowed_ids.append(field_id)
        label = field.get("label") or field_id
        ftype = field.get("type") or "text"
        value = field.get("value")
        options = field.get("options")
        desc = field.get("description") or ""
        entry = {
            "id": field_id,
            "label": label,
            "type": ftype,
            "value": value,
            "options": options,
            "description": desc,
        }
        field_descriptions.append(entry)

    system = (
        "You are a form assistant. Return ONLY valid JSON. "
        "Output an array of objects with keys: field_id, value, rationale (optional). "
        "Only use field_id values from the allowed list. "
        "Do not include markdown or extra text."
    )

    user_text = {
        "goal": prompt,
        "workflow": workflow,
        "scope": scope,
        "allowed_fields": allowed_ids,
        "fields": field_descriptions,
    }

    try:
        response = provider.predict(
            messages=[{"role": "user", "content": json.dumps(user_text)}],
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens"),
        )
    except Exception as exc:
        return jsonify({"error": "llm_request_failed", "details": str(exc)}), 400

    parsed = _extract_json_payload(response.text)
    if not isinstance(parsed, list):
        return jsonify({"error": "invalid_llm_response", "details": response.text[:500]}), 400

    cleaned = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        field_id = item.get("field_id") or item.get("id")
        if not field_id or field_id not in allowed_ids:
            continue
        cleaned.append(
            {
                "field_id": field_id,
                "value": item.get("value"),
                "rationale": item.get("rationale"),
            }
        )

    if not cleaned:
        return jsonify({"error": "no_suggestions"}), 400

    return jsonify({"suggestions": cleaned})


def _sse(entry: Dict[str, Any]) -> str:
    payload = json.dumps(entry)
    return f"data: {payload}\n\n"


if __name__ == "__main__":
    host = os.getenv("REFINER_HOST", "127.0.0.1")
    port = int(os.getenv("REFINER_PORT", "5001"))
    debug = os.getenv("REFINER_DEBUG", "0") in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug, threaded=True)
