"""
Project solver pipeline for Rag_Demo.

Scans a local project folder (or uses an explicit requirements document),
derives candidate requirements and context per source file, prompts an LLM for
a structured action plan, applies safe file edits, and optionally runs commands.
When the requirements do not specify an output location, a solver workspace
directory is created to contain new environments or generated code. Existing
solver workspaces are scanned for TODO/FIXME items and treated as their own
requirement sources. When a requirements.txt file is generated, versions are
normalized and updated via package index metadata unless disabled. All actions
are logged to a JSON report. When code-heavy plans are inadequate, optional
OpenCode/Codex CLI fallbacks can be invoked if configured.
"""

import ast
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from agentic_workflow import AgenticWorkflow, PhaseResult, ProgressTracker
from file_converter import FileConverter
from llm_providers import get_provider, LLMQuotaError, LLMError, request_category
from repo_context import RepoIndex
from web_research import (
    WebResearchCache,
    GoogleSearchEngine,
    fetch_url_content,
    normalize_query,
    search_web,
    summarize_web_research,
)

logger = logging.getLogger(__name__)


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".research_cache",
    "__pypackages__",
    "site-packages",
    "dist-packages",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "test_output",
    "project_solver_output",
}

DEFAULT_SOLVER_OUTPUT_DIR = "project_solver_output"

PYPI_JSON_CACHE: Dict[str, Dict[str, object]] = {}
CODEX_PREFLIGHT_STATE: Optional[Dict[str, object]] = None

REQUIREMENT_NAME_HINTS = (
    "readme",
    "requirement",
    "requirements",
    "todo",
    "roadmap",
    "spec",
    "design",
    "proposal",
    "backlog",
    "plan",
    "issue_template",
    "pull_request_template",
)

REQUIREMENT_DOC_HINTS = (
    "readme",
    "requirement",
    "requirements",
    "roadmap",
    "spec",
    "design",
    "proposal",
    "backlog",
    "plan",
    "issue_template",
    "pull_request_template",
)
TEXT_EXTS = {
    ".md",
    ".txt",
    ".rst",
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".sh",
    ".js",
    ".ts",
    ".tsx",
    ".html",
    ".css",
}

REQUIREMENT_LINE_RE = re.compile(
    r"\b("
    r"must|should|need to|needs to|required|requirement|todo|fixme|must not|shall|"
    r"ensure|please|in this task|in this final task|you need to|we need to|you have to|we have to"
    r")\b",
    re.IGNORECASE,
)
SOFT_REQUIREMENT_RE = re.compile(
    r"\b(improve|accuracy|distance|can you|how might you|how would you)\b",
    re.IGNORECASE,
)
TEST_ASSERT_RE = re.compile(r"^\s*assert\s+.+")

TODO_LINE_RE = re.compile(r"\b(todo|fixme|bug|xxx)\b", re.IGNORECASE)
CODE_REQUIREMENT_RE = re.compile(
    r"\b(implement|build|create|develop|refactor|rewrite|fix|bug|feature|module|class|function|script|cli|library|algorithm|api)\b",
    re.IGNORECASE,
)
NON_CODE_HINT_RE = re.compile(
    r"\b(deploy|deployment|infrastructure|infra|environment|config|configuration|documentation|docs|readme|requirements\.txt|install|dependency|dependencies|package|build pipeline|ci|cd|release)\b",
    re.IGNORECASE,
)
REQ_ID_RE = re.compile(r"\bREQ-\d{3,}\b", re.IGNORECASE)
SEQ_NAME_RE = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z0-9_-]*?)(?P<num>\d+)$")
SEQUENCE_REQUIREMENT_PREFIXES = {
    "task",
    "solution",
    "test_solution",
    "testsolution",
}
SAMPLE_CODE_HINTS = (
    "sample",
    "example",
    "starter",
    "template",
    "baseline",
    "skeleton",
    "scaffold",
)
SAMPLE_CODE_TEXT_HINTS = (
    "get started",
    "getting started",
    "starter code",
    "starting point",
    "skeleton code",
    "boilerplate",
    "seed code",
    "a colleague has written",
    "colleague has written",
    "provided code",
    "example implementation",
)
HELPER_MODULE_HINTS = (
    "helper",
    "utils",
    "util",
    "common",
)
KNOWN_HELPER_MODULES = {
    "pdf",
    "gemini",
    "nominatim",
}

GLOBAL_REQUIREMENTS = [
    {
        "key": "language",
        "title": "Language, terminology, professionalism",
        "description": "Use clear, professional language and correct terminology in code and documentation.",
        "type": "non-functional",
        "priority": "must",
        "acceptance_criteria": [
            "When producing natural language output, use UK British English spelling and grammar unless the target audience requires otherwise.",
            "When producing documents, default to A4 page size unless specified.",
            "Narrative text uses a professional, non-sycophantic tone.",
            "Preserve external library/API names and identifiers; do not translate code identifiers.",
            "Locale/timezone handling is configurable and defaults to the target audience or ISO-8601/UTC when unspecified.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "reuse_existing_material",
        "title": "Reuse relevant supplied material",
        "description": "Reuse and expand on relevant supplied documents, requirements, or code when available.",
        "type": "constraint",
        "priority": "must",
        "acceptance_criteria": [
            "If relevant material already exists at the start or after a restart, it is reused or expanded rather than recreated.",
            "If supplied material is not reused, the reason is stated with relevance noted.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "language_selection",
        "title": "Select coding language(s) appropriately",
        "description": "Use the pre-selected, existing, or best-fit language, defaulting to Rust when no fit is clear, and use multiple languages if required.",
        "type": "constraint",
        "priority": "must",
        "acceptance_criteria": [
            "If a language is specified, it is used.",
            "If existing code makes the language obvious, it is used.",
            "If no language is specified or obvious, the best-fit language for the goal is chosen; if unclear, default to Rust.",
            "Multiple languages are used when required for effectiveness, efficiency, or performance, with rationale noted.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "best_practices",
        "title": "Follow best practices and industry knowledge",
        "description": "Implement solutions that align with established software engineering best practices and relevant industry standards.",
        "type": "non-functional",
        "priority": "must",
        "acceptance_criteria": [
            "Design decisions reference a relevant best practice, standard, or rationale note.",
            "Implementation avoids known anti-patterns and uses proven approaches where applicable.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "robust_resilient",
        "title": "Build robust and resilient solutions",
        "description": "Solutions must handle expected and unexpected conditions gracefully and remain reliable under failure scenarios.",
        "type": "non-functional",
        "priority": "must",
        "acceptance_criteria": [
            "Expected failure modes are handled or explicitly documented with rationale.",
            "Failure handling does not corrupt data and fails safely.",
            "Operations that can be retried are idempotent or protected by guards when applicable.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "secure",
        "title": "Ensure secure implementations",
        "description": "Solutions must avoid common security risks and validate inputs/permissions appropriately.",
        "type": "non-functional",
        "priority": "must",
        "acceptance_criteria": [
            "Inputs are validated and sanitized where applicable.",
            "Sensitive data is not logged and is handled per security best practices.",
            "Access follows least-privilege where permissions are relevant.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "scalable",
        "title": "Design for scalability",
        "description": "Solutions must scale to expected data volumes and usage patterns without undue degradation.",
        "type": "non-functional",
        "priority": "should",
        "acceptance_criteria": [
            "Expected load or volume assumptions are documented when performance matters.",
            "No obvious bottlenecks remain for expected loads.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "modular_reusable",
        "title": "Modular and reusable design",
        "description": "Components should be modular, reusable, and avoid unnecessary duplication.",
        "type": "non-functional",
        "priority": "should",
        "acceptance_criteria": [
            "Logic is decomposed into cohesive components.",
            "Reusable utilities are extracted where appropriate and duplication is minimal.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "clean_code",
        "title": "Clean, maintainable code",
        "description": "Code should be clean, readable, and maintainable with consistent style.",
        "type": "non-functional",
        "priority": "must",
        "acceptance_criteria": [
            "Naming is clear and consistent.",
            "Non-obvious logic includes brief inline documentation.",
            "Formatting follows project conventions.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "functional_code",
        "title": "Functional code with examples",
        "description": "Code should be functional and include examples or tests demonstrating expected behaviour.",
        "type": "non-functional",
        "priority": "must",
        "acceptance_criteria": [
            "Code runs without errors and meets requirements.",
            "Includes at least one runnable example or test covering the main path.",
            "Includes at least one example or test for a failure or edge case when applicable.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "documentation",
        "title": "Detailed documentation and inline comments",
        "description": "Provide detailed documents and inline code comments where needed to explain non-obvious behaviour.",
        "type": "non-functional",
        "priority": "must",
        "acceptance_criteria": [
            "Key decisions, assumptions, and usage are documented in project docs.",
            "Limitations or known gaps are documented when present.",
            "Inline comments exist for non-obvious logic.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "cross_reference",
        "title": "Cross-reference requirements and changes",
        "description": "Cross-reference requirement IDs within plans, code comments, and documentation where possible.",
        "type": "constraint",
        "priority": "must",
        "acceptance_criteria": [
            "Plans or docs reference REQ-### IDs where possible.",
            "Code comments reference relevant REQ-### IDs when it adds clarity.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "sanity_check_requirements",
        "title": "Sanity-check requirements vs outcomes",
        "description": "Validate that implemented outputs match original requirements precisely, without scope creep or omissions.",
        "type": "constraint",
        "priority": "must",
        "acceptance_criteria": [
            "Post-implementation checks confirm each requirement is satisfied or explicitly not applicable.",
            "No extra functionality is added without requirement coverage or an agreed change.",
        ],
        "notes": "Global requirement.",
    },
    {
        "key": "error_tracking",
        "title": "Log errors and track unresolved work",
        "description": "Log errors, attempt resolution, and track unresolved items in a consolidated TODO list.",
        "type": "constraint",
        "priority": "must",
        "acceptance_criteria": [
            "Failures are recorded with context and outcomes.",
            "At least one remediation attempt is documented or a rationale is given for not attempting one.",
            "Unresolved failures are captured in a consolidated TODO list with status and next steps.",
        ],
        "notes": "Global requirement.",
    },
]

CODE_FILE_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".sql",
}

DATA_FILE_EXTS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
}

TEST_FILE_RE = re.compile(
    r"(^|/|\\\\)(tests|test)(/|\\\\)|(^|/|\\\\)(test_|spec_)|(_test\.|_spec\.)",
    re.IGNORECASE,
)

VERIFICATION_ERROR_RE = re.compile(
    r"(traceback \(most recent call last\)|\bexception\b|\bassertionerror\b|\berror processing\b|\berror:)",
    re.IGNORECASE,
)
NO_TESTS_RAN_RE = re.compile(r"(no tests ran|collected 0 items)", re.IGNORECASE)
FUTURE_WARNING_RE = re.compile(r"\bfuturewarning\s*:", re.IGNORECASE)
INDENTATION_ERROR_RE = re.compile(
    r"(indentationerror|taberror|unexpected indent|unindent does not match any outer indentation level)",
    re.IGNORECASE,
)
TRACEBACK_FILE_RE = re.compile(r'File \"([^\"]+)\", line (\\d+)')
MODULE_NOT_FOUND_RE = re.compile(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]")
NO_DATA_PROCESSED_RE = re.compile(
    r"(starting evaluation for\s*0 documents|total documents processed:\s*0|0 documents processed)",
    re.IGNORECASE,
)
ACCURACY_TARGET_RE = re.compile(r"accuracy target not met", re.IGNORECASE)
DEFAULT_FALLBACK_RE = re.compile(r"returning default", re.IGNORECASE)
EMPTY_OR_MALFORMED_RE = re.compile(r"empty or malformed", re.IGNORECASE)
PREDICTED_PAGE_RE = re.compile(r"predicted\s+page:\s*(\d+)", re.IGNORECASE)
PREDICTED_ROT_RE = re.compile(r"predicted\s+rotation:\s*([-\d]+)", re.IGNORECASE)
PREDICTED_LABEL_RE = re.compile(r"predicted:\s*([a-zA-Z0-9_ -]+)\s*\|\s*actual", re.IGNORECASE)
PREDICTED_COORD_RE = re.compile(r"predicted:\s*\(?\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)?", re.IGNORECASE)
LOG_FILE_ACTION_RE = re.compile(
    r"^(Wrote file|Appended file|Replaced text in file):\s+(.+?)(?:\s+\(abs: (.+)\))?$"
)
WEB_RESEARCH_TRIGGER_RE = re.compile(
    r"(traceback|exception|error|failed|module not found|importerror|modulenotfounderror)",
    re.IGNORECASE,
)

DEFAULT_OPENCODE_COMMAND_TEMPLATE = (
    "{opencode_bin} run --format json {opencode_model_flag} --file {prompt_file} "
    "\"Use the attached file as instructions. Respond ONLY with JSON matching the schema.\""
)


@dataclass
class RequirementSource:
    path: str
    requirements_text: str
    requirement_lines: List[str]
    todo_lines: List[str]
    context_excerpt: str


@dataclass
class ProjectScanResult:
    requirements_by_source: List[RequirementSource]
    sources: List[str]
    context_summary: str


def _is_subpath(root: str, candidate: str) -> bool:
    abs_root = os.path.abspath(root)
    abs_path = os.path.abspath(candidate)
    return abs_path == abs_root or abs_path.startswith(abs_root + os.sep)


def _display_path_for_report(project_root: str, abs_path: str) -> str:
    if _is_subpath(project_root, abs_path):
        return os.path.relpath(abs_path, project_root)
    return abs_path


def _safe_path(root: str, candidate: str, extra_roots: Optional[List[str]] = None) -> Optional[str]:
    abs_root = os.path.abspath(root)
    abs_path = os.path.abspath(candidate if os.path.isabs(candidate) else os.path.join(root, candidate))
    allowed_roots = [abs_root]
    if extra_roots:
        allowed_roots.extend([os.path.abspath(r) for r in extra_roots if r])
    for allowed in allowed_roots:
        if _is_subpath(allowed, abs_path):
            return abs_path
    return None


def _read_text_file(path: str, max_bytes: int) -> Optional[str]:
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except Exception as exc:
        logger.debug(f"Failed to read {path}: {exc}")
        return None


def _normalize_python_indentation(text: str) -> str:
    lines = text.splitlines()
    out: List[str] = []
    for line in lines:
        if not line:
            out.append("")
            continue
        leading = re.match(r"^[\\t ]*", line)
        prefix = leading.group(0) if leading else ""
        body = line[len(prefix) :]
        prefix = prefix.replace("\\t", " " * 4)
        out.append(prefix + body.rstrip())
    return "\n".join(out).rstrip() + "\n"


def _autopep8_format_text(text: str) -> Optional[str]:
    try:
        import autopep8  # type: ignore
    except Exception:
        return None
    try:
        return autopep8.fix_code(text)
    except Exception:
        return None


def _normalize_python_file(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            original = handle.read()
    except Exception:
        return False
    fixed = _normalize_python_indentation(original)
    if fixed == original:
        # Try autopep8 formatting if available and content is otherwise unchanged.
        fixed = _autopep8_format_text(fixed) or fixed
        if fixed == original:
            return False
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(fixed)
    except Exception:
        return False
    return True


def _extract_error_file_paths(output: str) -> List[str]:
    paths: List[str] = []
    for match in TRACEBACK_FILE_RE.finditer(output or ""):
        path = match.group(1)
        if path:
            paths.append(path)
    return paths


def _collect_sample_code_map(
    project_root: str,
    *,
    extra_ignored: Optional[List[str]] = None,
    max_files: int = 40,
    max_file_bytes: int = 200_000,
) -> Dict[str, str]:
    samples: Dict[str, str] = {}
    ignored = _ignored_dirnames(extra_ignored)
    count = 0
    for walk_root, dirs, files in os.walk(project_root):
        _filter_walk_dirs(walk_root, dirs, ignored)
        for filename in files:
            if count >= max_files:
                break
            ext = os.path.splitext(filename)[1].lower()
            if ext not in CODE_FILE_EXTS:
                continue
            rel_path = os.path.relpath(os.path.join(walk_root, filename), project_root)
            text = _read_text_file(os.path.join(walk_root, filename), max_file_bytes)
            if not text:
                continue
            if not _is_sample_code_name(rel_path) and not _is_sample_code_text(text):
                continue
            excerpt = "\n".join(text.splitlines()[:40]).strip()
            if excerpt:
                samples[rel_path] = excerpt
                count += 1
        if count >= max_files:
            break
    return samples


def _match_sample_code_for_source(
    source_path: str,
    sample_map: Dict[str, str],
    *,
    max_matches: int = 2,
) -> List[Tuple[str, str]]:
    if not sample_map:
        return []
    matches: List[Tuple[str, str]] = []
    source_stem = os.path.splitext(os.path.basename(source_path))[0].lower()
    source_match = SEQ_NAME_RE.match(source_stem)
    source_prefix = _normalize_sequence_prefix(source_match.group("prefix")) if source_match else ""
    source_num = source_match.group("num") if source_match else ""
    for path, excerpt in sample_map.items():
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if source_stem and source_stem in stem:
            matches.append((path, excerpt))
            if len(matches) >= max_matches:
                break
            continue
        if source_prefix and source_num and source_prefix in stem and source_num in stem:
            matches.append((path, excerpt))
            if len(matches) >= max_matches:
                break
    return matches


def _summarize_helper_module(text: str, rel_path: str) -> str:
    summary_parts: List[str] = []
    try:
        tree = ast.parse(text)
    except Exception:
        names = []
        for line in text.splitlines()[:200]:
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("class "):
                parts = stripped.split()
                if len(parts) >= 2:
                    names.append(parts[1].split("(")[0])
            if len(names) >= 6:
                break
        return ", ".join(names) if names else "helper module"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            doc = ast.get_docstring(node) or ""
            doc_line = doc.splitlines()[0] if doc else ""
            args = []
            for arg in node.args.args:
                if arg.arg in {"self", "cls"}:
                    continue
                args.append(arg.arg)
            signature = f"({', '.join(args)})" if args else "()"
            if node.name == "load_eval_data":
                summary_parts.append("load_eval_data(): loads eval_data.json into list[dict]")
            elif doc_line:
                summary_parts.append(f"{node.name}{signature}: {doc_line}")
            else:
                summary_parts.append(f"{node.name}{signature}")
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            doc_line = doc.splitlines()[0] if doc else ""
            fields = []
            methods = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
                if isinstance(item, ast.FunctionDef):
                    name = item.name
                    if name.startswith("__"):
                        continue
                    methods.append(name)
            fields_snippet = f" fields: {', '.join(fields[:6])}" if fields else ""
            methods_snippet = f" methods: {', '.join(methods[:6])}" if methods else ""
            if doc_line:
                summary_parts.append(f"{node.name}: {doc_line}{fields_snippet}{methods_snippet}")
            else:
                summary_parts.append(f"{node.name}{fields_snippet}{methods_snippet}")
        if len(summary_parts) >= 6:
            break

    if summary_parts:
        return "; ".join(summary_parts)
    return "helper module"


def _collect_helper_module_summaries(
    project_root: str,
    *,
    extra_ignored: Optional[List[str]] = None,
    max_files: int = 20,
) -> Dict[str, str]:
    ignored = _ignored_dirnames(extra_ignored)
    summaries: Dict[str, str] = {}
    count = 0
    for walk_root, dirs, files in os.walk(project_root):
        _filter_walk_dirs(walk_root, dirs, ignored)
        for filename in files:
            if count >= max_files:
                break
            if not filename.endswith(".py"):
                continue
            rel_path = os.path.relpath(os.path.join(walk_root, filename), project_root)
            base = os.path.splitext(filename)[0].lower()
            if TEST_FILE_RE.search(rel_path):
                continue
            if base.startswith("solution") or base.startswith("task"):
                continue
            if base not in KNOWN_HELPER_MODULES and not any(hint in base for hint in HELPER_MODULE_HINTS):
                continue
            text = _read_text_file(os.path.join(walk_root, filename), 200_000)
            if not text:
                continue
            summary = _summarize_helper_module(text, rel_path)
            summaries[rel_path] = summary
            count += 1
        if count >= max_files:
            break
    return summaries


def _collect_dataset_summary(
    project_root: str,
    *,
    extra_ignored: Optional[List[str]] = None,
    max_files: int = 60,
) -> Dict[str, object]:
    data_root = os.path.join(project_root, "data")
    if not os.path.isdir(data_root):
        return {"count": 0, "files": [], "paths": []}
    ignored = _ignored_dirnames(extra_ignored)
    files: List[str] = []
    paths: List[str] = []
    for walk_root, dirs, filenames in os.walk(data_root):
        _filter_walk_dirs(walk_root, dirs, ignored)
        for filename in filenames:
            if len(files) >= max_files:
                break
            ext = os.path.splitext(filename)[1].lower()
            if ext not in DATA_FILE_EXTS:
                continue
            rel_path = os.path.relpath(os.path.join(walk_root, filename), project_root)
            files.append(filename)
            paths.append(rel_path)
        if len(files) >= max_files:
            break
    return {"count": len(files), "files": files, "paths": paths}


def _collect_eval_data_schema(project_root: str) -> Dict[str, object]:
    eval_path = os.path.join(project_root, "eval_data.json")
    if not os.path.isfile(eval_path):
        return {}
    try:
        with open(eval_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return {"path": eval_path, "error": str(exc)}
    if not isinstance(data, list):
        return {"path": eval_path, "count": 0, "keys": [], "sample": None}
    sample = data[0] if data else None
    keys: List[str] = []
    types: Dict[str, str] = {}
    if isinstance(sample, dict):
        keys = list(sample.keys())
        for key, value in sample.items():
            if isinstance(value, list) and value:
                types[key] = f"list[{type(value[0]).__name__}]"
            else:
                types[key] = type(value).__name__
    label_values = None
    if isinstance(sample, dict) and "type" in sample:
        values = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                values.append(item["type"].strip())
        unique = sorted({val.lower() for val in values if val})
        if unique and len(unique) <= 10:
            label_values = unique
    return {
        "path": eval_path,
        "count": len(data),
        "keys": keys,
        "types": types,
        "sample": sample,
        "label_values": label_values,
    }


def _build_eval_data_section(eval_info: Dict[str, object]) -> str:
    if not eval_info:
        return ""
    if eval_info.get("error"):
        return f"Eval data schema: failed to read eval_data.json ({eval_info.get('error')}).\n\n"
    keys = eval_info.get("keys") or []
    types = eval_info.get("types") or {}
    sample = eval_info.get("sample")
    count = eval_info.get("count")
    key_parts = []
    for key in keys:
        type_name = types.get(key)
        if type_name:
            key_parts.append(f"{key} ({type_name})")
        else:
            key_parts.append(key)
    keys_line = ", ".join(key_parts) if key_parts else "unknown"
    sample_line = ""
    if isinstance(sample, dict):
        trimmed = dict(list(sample.items())[:5])
        sample_line = f"Example record (truncated): {trimmed}"
    label_line = ""
    label_values = eval_info.get("label_values")
    if isinstance(label_values, list) and label_values:
        label_line = f"- Label values for 'type': {', '.join(label_values)}\n"
    list_line = ""
    list_keys = []
    for key, type_name in (eval_info.get("types") or {}).items():
        if isinstance(type_name, str) and type_name.startswith("list["):
            list_keys.append(f"{key} ({type_name})")
    if list_keys:
        list_line = f"- List-valued keys: {', '.join(list_keys)}\n"
    return (
        "Eval data schema (from eval_data.json):\n"
        f"- Records: {count}\n"
        f"- Keys: {keys_line}\n"
        f"{label_line}"
        f"{list_line}"
        f"{sample_line}\n\n"
    )


def _collect_related_test_snippets(
    source_path: str,
    project_root: str,
    *,
    extra_ignored: Optional[List[str]] = None,
    max_matches: int = 2,
    max_lines: int = 16,
) -> List[Tuple[str, str]]:
    if not source_path:
        return []
    source_stem = os.path.splitext(os.path.basename(source_path))[0].lower()
    source_match = SEQ_NAME_RE.match(source_stem)
    if not source_match:
        return []
    prefix = _normalize_sequence_prefix(source_match.group("prefix"))
    num = source_match.group("num")
    if not num:
        return []
    candidates: List[str] = []
    if prefix in {"task", "solution"}:
        candidates.extend(
            [
                f"test_solution{num}.py",
                f"tests/test_solution{num}.py",
                f"test_task{num}.py",
                f"tests/test_task{num}.py",
            ]
        )
    elif prefix in {"test_solution", "testsolution"}:
        candidates.extend([f"test_solution{num}.py", f"tests/test_solution{num}.py"])
    matches: List[Tuple[str, str]] = []
    ignored = _ignored_dirnames(extra_ignored)
    for rel_path in candidates:
        abs_path = os.path.join(project_root, rel_path)
        if not os.path.isfile(abs_path):
            continue
        if any(part in ignored for part in rel_path.split(os.sep)):
            continue
        text = _read_text_file(abs_path, 200_000)
        if not text:
            continue
        assert_lines = [line for line in text.splitlines() if TEST_ASSERT_RE.search(line)]
        if assert_lines:
            snippet = "\n".join(assert_lines[:max_lines])
        else:
            snippet = "\n".join(text.splitlines()[:max_lines])
        matches.append((rel_path, snippet))
        if len(matches) >= max_matches:
            break
    return matches


def _ignored_dirnames(extra_ignored: Optional[List[str]] = None) -> set:
    ignored = set(IGNORED_DIRS)
    if not extra_ignored:
        return ignored
    for item in extra_ignored:
        if not item:
            continue
        cleaned = item.strip().lstrip("./")
        if not cleaned:
            continue
        first = cleaned.split(os.sep)[0]
        if first:
            ignored.add(first)
    return ignored


def _normalize_sequence_prefix(prefix: str) -> str:
    cleaned = (prefix or "").strip().lower().replace("-", "_")
    return cleaned.rstrip("_")


def _is_sequence_requirement_name(path: str) -> bool:
    stem = os.path.splitext(os.path.basename(path))[0]
    match = SEQ_NAME_RE.match(stem)
    if not match:
        return False
    prefix = _normalize_sequence_prefix(match.group("prefix"))
    return prefix in SEQUENCE_REQUIREMENT_PREFIXES


def _is_sample_code_name(path: str) -> bool:
    name = os.path.basename(path).lower()
    return any(hint in name for hint in SAMPLE_CODE_HINTS)


def _is_sample_code_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in SAMPLE_CODE_TEXT_HINTS)


def _merge_notes(*notes: Optional[str]) -> Optional[str]:
    parts = [note for note in notes if note]
    return " ".join(parts) if parts else None


def _correct_sequence_plural_path(
    rel_path: str,
    *,
    project_root: str,
    workspace_root: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    if os.path.isabs(rel_path):
        return None, None
    directory = os.path.dirname(rel_path)
    base = os.path.basename(rel_path)
    stem, ext = os.path.splitext(base)
    if not stem or not ext:
        return None, None
    for prefix in sorted(SEQUENCE_REQUIREMENT_PREFIXES, key=len, reverse=True):
        plural_prefix = f"{prefix}s"
        if not stem.startswith(plural_prefix):
            continue
        suffix = stem[len(plural_prefix):]
        if not suffix.isdigit():
            continue
        candidate_base = f"{prefix}{suffix}{ext}"
        candidate_rel = os.path.join(directory, candidate_base) if directory else candidate_base
        project_candidate = os.path.join(project_root, candidate_rel)
        if os.path.exists(project_candidate):
            return candidate_rel, f"Corrected pluralized sequence filename: {rel_path} -> {candidate_rel}"
        if workspace_root:
            workspace_candidate = os.path.join(workspace_root, candidate_rel)
            if os.path.exists(workspace_candidate):
                return candidate_rel, f"Corrected pluralized sequence filename: {rel_path} -> {candidate_rel}"
        return candidate_rel, f"Normalized sequence filename to singular: {rel_path} -> {candidate_rel}"
    return None, None


def _correct_sequence_plural_abs_path(
    abs_path: str,
    *,
    project_root: str,
    workspace_root: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    if not os.path.isabs(abs_path):
        return None, None
    base = os.path.basename(abs_path)
    match = re.match(r"^(?P<prefix>[A-Za-z_]+)s(?P<num>\d+)(?P<ext>\.\w+)$", base)
    if not match:
        return None, None
    prefix = match.group("prefix").lower()
    num = match.group("num")
    ext = match.group("ext")
    normalized_prefix = _normalize_sequence_prefix(prefix)
    if normalized_prefix not in SEQUENCE_REQUIREMENT_PREFIXES or not num:
        return None, None
    candidate_base = f"{normalized_prefix}{num}{ext}"
    candidate_abs = os.path.join(os.path.dirname(abs_path), candidate_base)
    if _is_subpath(project_root, candidate_abs):
        return candidate_abs, f"Corrected pluralized sequence filename: {abs_path} -> {candidate_abs}"
    if workspace_root and _is_subpath(workspace_root, candidate_abs):
        return candidate_abs, f"Corrected pluralized sequence filename: {abs_path} -> {candidate_abs}"
    return candidate_abs, f"Normalized sequence filename to singular: {abs_path} -> {candidate_abs}"


def _filter_walk_dirs(walk_root: str, dirs: List[str], ignored: set) -> None:
    keep: List[str] = []
    for name in dirs:
        if name in ignored:
            continue
        candidate = os.path.join(walk_root, name)
        if _looks_like_venv_dir(candidate):
            continue
        keep.append(name)
    dirs[:] = keep


def _resolve_file_target(
    rel_path: str,
    *,
    project_root: str,
    workspace_root: Optional[str],
    step_type: str,
    prefer_workspace_new_files: bool,
) -> Tuple[str, Optional[str]]:
    correction_note = None
    if os.path.isabs(rel_path):
        corrected_abs, correction_note = _correct_sequence_plural_abs_path(
            rel_path, project_root=project_root, workspace_root=workspace_root
        )
        if corrected_abs:
            rel_path = corrected_abs
        if os.path.isabs(rel_path) or not workspace_root:
            return rel_path, correction_note
    corrected_path, correction_note = _correct_sequence_plural_path(
        rel_path, project_root=project_root, workspace_root=workspace_root
    )
    if corrected_path:
        rel_path = corrected_path
    if os.path.isabs(rel_path) or not workspace_root:
        return rel_path, correction_note
    _, ext = os.path.splitext(rel_path)
    is_code_or_test = ext.lower() in CODE_FILE_EXTS or bool(TEST_FILE_RE.search(rel_path))
    workspace_outside_project = not _is_subpath(project_root, workspace_root)
    project_candidate = os.path.join(project_root, rel_path)
    if os.path.exists(project_candidate):
        workspace_candidate = os.path.join(workspace_root, rel_path)
        if workspace_outside_project and os.path.exists(workspace_candidate):
            return rel_path, _merge_notes(
                correction_note,
                f"Duplicate file exists in solver workspace; using project root: {rel_path}",
            )
        return rel_path, correction_note
    workspace_candidate = os.path.join(workspace_root, rel_path)
    if os.path.exists(workspace_candidate):
        if workspace_outside_project and is_code_or_test:
            return (
                rel_path,
                _merge_notes(
                    correction_note,
                    f"Solver workspace has {rel_path}, but code/test stays in project root; "
                    f"leave workspace copy untouched.",
                ),
            )
        return workspace_candidate, _merge_notes(
            correction_note,
            f"Redirected file step to solver workspace: {rel_path} -> {workspace_candidate}",
        )
    if step_type == "write_file" and prefer_workspace_new_files:
        if workspace_outside_project and is_code_or_test:
            return rel_path, _merge_notes(
                correction_note,
                f"Keeping new code/test file in project root: {rel_path}",
            )
        return workspace_candidate, _merge_notes(
            correction_note,
            f"Redirected new file to solver workspace: {rel_path} -> {workspace_candidate}",
        )
    return rel_path, correction_note


def _line_number_for_text(text: str, target: str) -> int:
    if not target:
        return 1
    idx = text.find(target)
    if idx < 0:
        return 1
    return text[:idx].count("\n") + 1


def _file_ref_from_log_entry(entry: str) -> Optional[Dict[str, object]]:
    match = LOG_FILE_ACTION_RE.match(entry.strip())
    if not match:
        return None
    action, rel_path, abs_path = match.groups()
    path = abs_path or rel_path
    note = action.lower().replace(" ", "_")
    return {
        "path": path,
        "abs_path": path,
        "line": 1,
        "note": f"{note}_log",
        "is_code": os.path.splitext(rel_path)[1].lower() in CODE_FILE_EXTS
        or bool(TEST_FILE_RE.search(rel_path)),
    }


def _is_candidate_file(path: str) -> bool:
    name = os.path.basename(path).lower()
    if _is_sequence_requirement_name(path):
        return True
    if TEST_FILE_RE.search(path):
        return True
    if any(hint in name for hint in REQUIREMENT_NAME_HINTS):
        return True
    ext = os.path.splitext(name)[1]
    if ext in (".md", ".txt"):
        return True
    lower_path = path.lower()
    if "docs" in lower_path:
        return True
    if "issue_template" in lower_path or "pull_request_template" in lower_path:
        return True
    return False


def _is_requirement_doc_name(path: str) -> bool:
    name = os.path.basename(path).lower()
    return any(hint in name for hint in REQUIREMENT_DOC_HINTS)


def _extract_requirement_lines(text: str, max_lines: int = 80) -> List[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if REQUIREMENT_LINE_RE.search(stripped):
            lines.append(stripped)
        if len(lines) >= max_lines:
            break
    return lines


def _extract_soft_requirement_lines(text: str, max_lines: int = 80) -> List[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if SOFT_REQUIREMENT_RE.search(stripped):
            lines.append(stripped)
        if len(lines) >= max_lines:
            break
    return lines


def _extract_test_assert_lines(text: str, max_lines: int = 60) -> List[str]:
    lines = []
    for line in text.splitlines():
        if TEST_ASSERT_RE.match(line):
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        if len(lines) >= max_lines:
            break
    return lines


def _extract_comment_todo(line: str) -> str:
    if not line:
        return ""
    markers = ("#", "//", "/*", "*", "<!--")
    idx = None
    for marker in markers:
        pos = line.find(marker)
        if pos != -1 and (idx is None or pos < idx):
            idx = pos
    if idx is None:
        return ""
    comment = line[idx:].strip()
    if not TODO_LINE_RE.search(comment):
        return ""
    return comment


def _extract_todo_lines(text: str, max_lines: int = 80) -> List[str]:
    lines = []
    max_chars = _env_int("SOLVER_TODO_MAX_CHARS", 200)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 1000:
            continue
        if TODO_LINE_RE.search(stripped):
            comment = _extract_comment_todo(stripped)
            if not comment:
                continue
            if max_chars and len(comment) > max_chars:
                comment = comment[:max_chars].rstrip() + "..."
            lines.append(comment)
        if len(lines) >= max_lines:
            break
    return lines


def _load_previous_output(output_path: str) -> Optional[Dict[str, object]]:
    if not output_path or not os.path.exists(output_path):
        return None
    try:
        with open(output_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.info(f"Failed to read previous solver output: {exc}")
        return None


def _extract_source_logs(actions_log: List[str]) -> Dict[str, List[str]]:
    source_logs: Dict[str, List[str]] = {}
    current_source: Optional[str] = None
    for entry in actions_log:
        if entry.startswith("Starting requirement source:"):
            current_source = entry.split(":", 1)[1].strip()
            source_logs.setdefault(current_source, []).append(entry)
            continue
        if current_source:
            source_logs[current_source].append(entry)
        if entry.startswith("Completed requirement source:"):
            current_source = None
    return source_logs


def _extract_completed_sources(actions_log: List[str]) -> List[str]:
    completed = []
    for entry in actions_log:
        if entry.startswith("Completed requirement source:"):
            completed.append(entry.split(":", 1)[1].strip())
    return completed


def _is_requirements_file(path: str) -> bool:
    return os.path.basename(path).lower() == "requirements.txt"


def _should_lookup_pypi() -> bool:
    if os.getenv("PIP_NO_INDEX"):
        return False
    disabled = os.getenv("DISABLE_PYPI_LOOKUP")
    if disabled and disabled.strip().lower() not in ("0", "false", "no"):
        return False
    return True


def _normalize_index_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith("/simple"):
        cleaned = cleaned[: -len("/simple")]
    if cleaned.endswith("/pypi"):
        cleaned = cleaned[: -len("/pypi")]
    return cleaned


def _get_index_urls() -> List[str]:
    urls: List[str] = []
    primary = os.getenv("PIP_INDEX_URL")
    if primary:
        urls.append(primary)
    extra = os.getenv("PIP_EXTRA_INDEX_URL")
    if extra:
        urls.extend(re.split(r"[,\s]+", extra.strip()))
    if not urls:
        urls = ["https://pypi.org/pypi"]
    normalized = []
    for url in urls:
        if not url:
            continue
        normalized.append(_normalize_index_url(url))
    return list(dict.fromkeys(normalized))


def _fetch_pypi_metadata(package: str) -> Optional[Dict[str, object]]:
    if package in PYPI_JSON_CACHE:
        return PYPI_JSON_CACHE[package]
    index_urls = _get_index_urls()
    timeout = float(os.getenv("PYPI_LOOKUP_TIMEOUT", "8"))
    missing_only = True
    had_error = False
    for base in index_urls:
        url = f"{base}/pypi/{package}/json"
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                PYPI_JSON_CACHE[package] = data
                return data
            if resp.status_code != 404:
                had_error = True
                missing_only = False
        except Exception as exc:
            logger.info(f"PyPI lookup failed for {package} at {base}: {exc}")
            had_error = True
            missing_only = False
    if had_error:
        return {"_lookup_error": True}
    if missing_only and index_urls:
        return {"_missing": True}
    return None


def _python_version_satisfies(specifier: Optional[str]) -> bool:
    if not specifier:
        return True
    try:
        from packaging.specifiers import SpecifierSet
    except Exception:
        return True
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    try:
        return SpecifierSet(specifier).contains(py_version)
    except Exception:
        return True


def _parse_version_value(version_text: str):
    try:
        from packaging.version import Version
        return Version(version_text)
    except Exception:
        return version_text


def _select_best_version(
    metadata: Dict[str, object],
    specifier: Optional[str],
    allow_prereleases: bool,
) -> Optional[str]:
    releases = metadata.get("releases") if isinstance(metadata, dict) else None
    if not isinstance(releases, dict):
        return None
    versions = list(releases.keys())
    parsed_versions = []
    for ver in versions:
        parsed = _parse_version_value(ver)
        parsed_versions.append((parsed, ver))
    parsed_versions.sort(key=lambda item: item[0], reverse=True)

    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
    except Exception:
        SpecifierSet = None
        Version = None

    spec = None
    if specifier and SpecifierSet:
        try:
            spec = SpecifierSet(specifier)
        except Exception:
            spec = None

    selected = None
    for parsed, ver in parsed_versions:
        if Version and isinstance(parsed, Version) and parsed.is_prerelease and not allow_prereleases:
            continue
        files = releases.get(ver) or []
        if files:
            ok = False
            for file_info in files:
                requires_python = file_info.get("requires_python") if isinstance(file_info, dict) else None
                if _python_version_satisfies(requires_python):
                    ok = True
                    break
            if not ok:
                continue
            if all(isinstance(f, dict) and f.get("yanked") for f in files):
                continue
        if spec and Version:
            try:
                if not spec.contains(Version(ver), prereleases=allow_prereleases):
                    continue
            except Exception:
                pass
        selected = ver
        break

    if not selected and parsed_versions:
        selected = parsed_versions[0][1]
    return selected


def _parse_requirement_line(line: str) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith(("-", "--")):
        return None
    if "://" in stripped or stripped.startswith("git+"):
        return None
    if "@" in stripped and "://" in stripped:
        return None
    try:
        from packaging.requirements import Requirement
        req = Requirement(stripped)
        name = req.name
        specifier = str(req.specifier) if req.specifier else ""
        extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
        marker = f"; {req.marker}" if req.marker else ""
        return name, extras, specifier, marker
    except Exception:
        match = re.match(r"^([A-Za-z0-9_.-]+)(\[[^\]]+\])?\s*([^;]*)?(;.*)?$", stripped)
        if not match:
            return None
        name = match.group(1)
        extras = match.group(2) or ""
        specifier = (match.group(3) or "").strip()
        marker = match.group(4) or ""
        return name, extras, specifier, marker


def _split_multi_requirements_line(line: str) -> Optional[List[str]]:
    if "," not in line:
        return None
    if re.search(r"\[[^\]]*,[^\]]*\]", line):
        return None
    if "@" in line:
        return None
    if re.search(r"(==|~=|!=|<=|>=|<|>)", line):
        return None
    if ";" in line:
        return None
    parts = [part.strip() for part in line.split(",") if part.strip()]
    if len(parts) <= 1:
        return None
    for part in parts:
        if not re.match(r"^[A-Za-z0-9_.-]+(\\[[^\\]]+\\])?$", part):
            return None
    return parts


def _update_requirements_versions(
    path: str,
    actions_log: List[str],
    *,
    treat_missing_as_hallucination: bool = False,
    hallucination_log: Optional[List[str]] = None,
) -> List[str]:
    if not _should_lookup_pypi():
        actions_log.append("Skipped PyPI lookup (disabled).")
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except Exception as exc:
        actions_log.append(f"Failed to read requirements for update: {exc}")
        return []

    normalized_lines = []
    normalized = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(("-", "--")):
            normalized_lines.append(line if line.endswith("\n") else line + "\n")
            continue
        base_line = line
        comment = ""
        if "#" in line and not stripped.startswith("#"):
            base_line, comment = line.split("#", 1)
            base_line = base_line.rstrip()
            comment = "#" + comment.rstrip("\n")
        multi = _split_multi_requirements_line(base_line)
        if multi:
            normalized = True
            for idx, item in enumerate(multi):
                entry = item
                if idx == 0 and comment:
                    entry = f"{entry} {comment}"
                normalized_lines.append(entry + "\n")
            continue
        normalized_lines.append(line if line.endswith("\n") else line + "\n")

    if normalized:
        actions_log.append("Normalized comma-separated requirement lines into one-per-line entries.")

    updated_lines = []
    changed = normalized
    updated_packages = []
    hallucinated_packages: List[str] = []
    for line in normalized_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            updated_lines.append(line)
            continue
        if stripped.startswith(("-", "--")):
            updated_lines.append(line)
            continue
        comment = ""
        if "#" in line and not stripped.startswith("#"):
            parts = line.split("#", 1)
            candidate = parts[0].rstrip()
            if candidate:
                line = candidate
                comment = "#" + parts[1].rstrip("\n")
        parsed = _parse_requirement_line(line)
        if not parsed:
            updated_lines.append(line.rstrip("\n") + ("\n" if not line.endswith("\n") else "") + (f" {comment}" if comment else ""))
            continue
        name, extras, specifier, marker = parsed
        if "@" in line:
            updated_lines.append(line.rstrip("\n") + ("\n" if not line.endswith("\n") else "") + (f" {comment}" if comment else ""))
            continue
        if specifier.startswith("==") or specifier.startswith("==="):
            if treat_missing_as_hallucination:
                metadata = _fetch_pypi_metadata(name)
                if metadata and metadata.get("_lookup_error"):
                    updated_lines.append(
                        line.rstrip("\n")
                        + ("\n" if not line.endswith("\n") else "")
                        + (f" {comment}" if comment else "")
                    )
                    continue
                if metadata and metadata.get("_missing"):
                    changed = True
                    hallucinated_packages.append(f"{name}{extras}{specifier}")
                    continue
                if metadata:
                    pinned_version = specifier.lstrip("=")
                    if pinned_version not in (metadata.get("releases") or {}):
                        changed = True
                        hallucinated_packages.append(f"{name}{extras}{specifier}")
                        continue
            updated_lines.append(line.rstrip("\n") + ("\n" if not line.endswith("\n") else "") + (f" {comment}" if comment else ""))
            continue
        allow_prereleases = "a" in specifier or "b" in specifier or "rc" in specifier
        metadata = _fetch_pypi_metadata(name)
        if metadata and metadata.get("_lookup_error"):
            updated_lines.append(line.rstrip("\n") + ("\n" if not line.endswith("\n") else "") + (f" {comment}" if comment else ""))
            continue
        if metadata and metadata.get("_missing"):
            if treat_missing_as_hallucination:
                changed = True
                hallucinated_packages.append(f"{name}{extras}")
                continue
            updated_lines.append(line.rstrip("\n") + ("\n" if not line.endswith("\n") else "") + (f" {comment}" if comment else ""))
            continue
        if not metadata:
            updated_lines.append(line.rstrip("\n") + ("\n" if not line.endswith("\n") else "") + (f" {comment}" if comment else ""))
            continue
        best_version = _select_best_version(metadata, specifier or None, allow_prereleases)
        if not best_version:
            updated_lines.append(line.rstrip("\n") + ("\n" if not line.endswith("\n") else "") + (f" {comment}" if comment else ""))
            continue
        updated_line = f"{name}{extras}=={best_version}"
        if marker:
            updated_line += marker
        if comment:
            updated_line += f" {comment}"
        updated_lines.append(updated_line + "\n")
        if updated_line.strip() != stripped:
            changed = True
            updated_packages.append(f"{name}=={best_version}")

    if changed:
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(updated_lines)
            if updated_packages:
                actions_log.append(
                    f"Updated requirements with latest compatible versions: {', '.join(updated_packages)}"
                )
            elif normalized:
                actions_log.append("Updated requirements file after normalization.")
            if hallucinated_packages:
                actions_log.append(
                    "Removed hallucinated requirements not found in configured indexes: "
                    + ", ".join(hallucinated_packages)
                )
        except Exception as exc:
            actions_log.append(f"Failed to write updated requirements: {exc}")
    else:
        actions_log.append("Requirements versions already up to date or unchanged.")
        if hallucinated_packages:
            actions_log.append(
                "Detected hallucinated requirements but no file changes were applied: "
                + ", ".join(hallucinated_packages)
            )
    if hallucinated_packages and hallucination_log is not None:
        hallucination_log.extend(hallucinated_packages)
    return hallucinated_packages


def _extract_plan_state(plans: List[object]) -> Dict[str, Dict[str, object]]:
    state: Dict[str, Dict[str, object]] = {}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        source_path = plan.get("source_path")
        if not isinstance(source_path, str):
            continue
        entry = state.setdefault(source_path, {"last_iteration": 0, "done": False})
        iteration = plan.get("iteration")
        if isinstance(iteration, int) and iteration > entry["last_iteration"]:
            entry["last_iteration"] = iteration
        if plan.get("done") is True:
            entry["done"] = True
    return state


def _normalize_step_type(step_type: Optional[str]) -> Optional[str]:
    if not step_type:
        return None
    if not isinstance(step_type, str):
        return None
    allowed = {"note", "write_file", "append_file", "replace_in_file", "run_command", "create_dir"}
    if step_type in allowed:
        return step_type
    if "|" in step_type:
        parts = [part.strip() for part in step_type.split("|") if part.strip()]
        priority = ["run_command", "create_dir", "write_file", "append_file", "replace_in_file", "note"]
        for candidate in priority:
            if candidate in parts:
                return candidate
    return step_type


def _find_workspace_venv(workspace: Optional[str]) -> Optional[str]:
    if not workspace or not os.path.isdir(workspace):
        return None
    for name in ("venv", ".venv"):
        candidate = os.path.join(workspace, name)
        if _looks_like_venv_dir(candidate):
            return candidate
    for root, dirs, _ in os.walk(workspace):
        for d in dirs:
            candidate = os.path.join(root, d)
            if _looks_like_venv_dir(candidate):
                return candidate
        break
    return None


def _rewrite_command_for_venv(command: str, venv_path: Optional[str]) -> str:
    if not command or not venv_path:
        return command
    contains_venv_create = bool(re.search(r"\b-m\s+venv\b", command))
    bin_dir = os.path.join(venv_path, "bin")
    python_exe = os.path.join(bin_dir, "python")
    pip_exe = os.path.join(bin_dir, "pip")
    python_exe = python_exe if os.path.exists(python_exe) else None
    pip_exe = pip_exe if os.path.exists(pip_exe) else None

    pip_pattern = re.compile(
        r"(^|[\s;&|])(?P<cmd>(?<!-m\s)pip3?|python3?\s+-m\s+pip)(?=$|[\s;&|])"
    )
    if pip_pattern.search(command):
        replacement = None
        if python_exe:
            replacement = f"{python_exe} -m pip"
        elif pip_exe:
            replacement = pip_exe
        if replacement:
            def _repl(match: re.Match) -> str:
                prefix = match.group(1)
                return f"{prefix}{replacement}"
            command = pip_pattern.sub(_repl, command)

    if contains_venv_create:
        return command

    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts:
        return command
    first = parts[0]
    if os.path.isabs(first) or first.startswith("./"):
        return command
    venv_exec = os.path.join(bin_dir, first)
    if os.path.exists(venv_exec):
        parts[0] = venv_exec
        return " ".join(shlex.quote(p) for p in parts)
    return command


def _rewrite_requirements_path_in_command(
    command: str,
    *,
    abs_workdir: str,
    workspace_root: Optional[str],
    actions_log: List[str],
) -> str:
    if not command or not workspace_root:
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts:
        return command
    changed = False
    for idx, token in enumerate(parts):
        if token in ("-r", "--requirement") and idx + 1 < len(parts):
            req_path = parts[idx + 1]
            if not os.path.isabs(req_path):
                candidate = os.path.join(abs_workdir, req_path)
                workspace_candidate = os.path.join(workspace_root, req_path)
                if not os.path.exists(candidate) and os.path.exists(workspace_candidate):
                    parts[idx + 1] = workspace_candidate
                    changed = True
        elif token.startswith("-r") and len(token) > 2:
            req_path = token[2:]
            if not os.path.isabs(req_path):
                candidate = os.path.join(abs_workdir, req_path)
                workspace_candidate = os.path.join(workspace_root, req_path)
                if not os.path.exists(candidate) and os.path.exists(workspace_candidate):
                    parts[idx] = "-r" + workspace_candidate
                    changed = True
    if not changed:
        return command
    rewritten = " ".join(shlex.quote(part) for part in parts)
    actions_log.append(
        f"Rewrote requirements path to solver workspace: {command} -> {rewritten}"
    )
    return rewritten


def _sanitize_shell_command(command: str, venv_path: Optional[str]) -> str:
    if not command:
        return command
    if command.startswith("syntax_check:"):
        target = command.split(":", 1)[1].strip()
        if target:
            return f"python -m py_compile {shlex.quote(target)}"
    sanitized = command.replace("'&&'", "&&").replace('"&&"', "&&")
    if "-m venv" in sanitized:
        match = re.search(r"(?P<py>\S*python(?:3)?)\s+-m\s+venv\s+(?P<target>\S+)", sanitized)
        if match:
            py = match.group("py")
            target = match.group("target")
            if venv_path:
                venv_norm = os.path.abspath(venv_path)
                target_norm = os.path.abspath(target)
                py_norm = os.path.abspath(py) if os.path.isabs(py) else py
                if target_norm == venv_norm and isinstance(py_norm, str) and py_norm.startswith(venv_norm):
                    sanitized = sanitized.replace(py, "python3", 1)
            elif os.path.isabs(py) and "/venv/" in py.replace("\\", "/"):
                sanitized = sanitized.replace(py, "python3", 1)
    return sanitized


def _is_activation_command(command: str, venv_path: Optional[str]) -> bool:
    if not command or not venv_path:
        return False
    tokens = command.strip().split()
    if len(tokens) < 2:
        return False
    if tokens[0] not in ("source", "."):
        return False
    target = tokens[1]
    target_norm = os.path.normpath(target)
    if target_norm.endswith(os.path.join("bin", "activate")) or target_norm.endswith(os.path.join("Scripts", "activate")):
        return True
    target_path = os.path.abspath(target)
    expected = os.path.join(os.path.abspath(venv_path), "bin", "activate")
    return target_path == expected


def _summarize_failures(failures: List[Dict[str, object]], max_chars: int = 10240) -> str:
    lines = []
    for idx, failure in enumerate(failures, start=1):
        command = failure.get("command")
        workdir = failure.get("workdir")
        exit_code = failure.get("exit_code")
        verification_issue = failure.get("verification_issue")
        stdout = (failure.get("stdout") or "").strip()
        stderr = (failure.get("stderr") or "").strip()
        if stdout and len(stdout) > max_chars:
            stdout = stdout[:max_chars] + "...(truncated)"
        if stderr and len(stderr) > max_chars:
            stderr = stderr[:max_chars] + "...(truncated)"
        verification_note = f"\nVerification issue: {verification_issue}" if verification_issue else ""
        lines.append(
            f"Failure {idx}:\nCommand: {command}\nWorkdir: {workdir}\nExit: {exit_code}{verification_note}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    return "\n\n".join(lines)


def _trim_actions_log(log: List[str], limit: int = 20) -> str:
    if len(log) <= limit:
        return "\n".join(log)
    return "\n".join(log[-limit:])


def _scan_requirements_root(
    root: str,
    *,
    root_display: Optional[str],
    max_files: int,
    max_file_bytes: int,
    max_context_files: int,
    max_code_files: int,
    extra_ignored: Optional[List[str]] = None,
) -> Tuple[List[RequirementSource], List[str]]:
    sources_by_path: Dict[str, Dict[str, object]] = {}
    context_blocks: List[str] = []

    def _display_path(rel_path: str) -> str:
        if not root_display:
            return rel_path
        return os.path.normpath(os.path.join(root_display, rel_path))

    def _get_entry(path: str) -> Dict[str, object]:
        entry = sources_by_path.get(path)
        if entry is None:
            entry = {
                "path": path,
                "requirement_lines": [],
                "todo_lines": [],
                "context_excerpt": "",
                "is_requirement_doc": False,
            }
            sources_by_path[path] = entry
        return entry

    ignored = _ignored_dirnames(extra_ignored)
    candidate_matches = 0
    for walk_root, dirs, files in os.walk(root):
        _filter_walk_dirs(walk_root, dirs, ignored)
        for filename in files:
            if candidate_matches >= max_files:
                break
            path = os.path.join(walk_root, filename)
            rel_path = os.path.relpath(path, root)
            if not _is_candidate_file(rel_path):
                continue
            text = _read_text_file(path, max_file_bytes)
            if not text:
                continue
            candidate_matches += 1
            display_path = _display_path(rel_path)
            entry = _get_entry(display_path)
            entry["is_requirement_doc"] = (
                entry["is_requirement_doc"]
                or _is_requirement_doc_name(rel_path)
                or _is_sequence_requirement_name(rel_path)
            )
            req_lines = _extract_requirement_lines(text)
            if not req_lines and (
                _is_sequence_requirement_name(rel_path) or _is_requirement_doc_name(rel_path)
            ):
                req_lines = _extract_soft_requirement_lines(text)
            if TEST_FILE_RE.search(rel_path):
                assert_lines = _extract_test_assert_lines(text)
                if assert_lines:
                    req_lines.extend([f"Test expectation: {line}" for line in assert_lines])
                    entry["is_requirement_doc"] = True
            if req_lines:
                entry["requirement_lines"].extend(req_lines[:40])
            if len(context_blocks) < max_context_files:
                context_excerpt = "\n".join(text.splitlines()[:40]).strip()
                if context_excerpt:
                    context_blocks.append(f"Excerpt from {display_path}:\n{context_excerpt}")
            if not entry.get("context_excerpt"):
                context_excerpt = "\n".join(text.splitlines()[:40]).strip()
                if context_excerpt:
                    entry["context_excerpt"] = context_excerpt
        if candidate_matches >= max_files:
            break

    todo_matches = 0
    for walk_root, dirs, files in os.walk(root):
        _filter_walk_dirs(walk_root, dirs, ignored)
        for filename in files:
            if todo_matches >= max_code_files:
                break
            ext = os.path.splitext(filename)[1].lower()
            if ext not in TEXT_EXTS:
                continue
            path = os.path.join(walk_root, filename)
            rel_path = os.path.relpath(path, root)
            text = _read_text_file(path, max_file_bytes)
            if not text:
                continue
            todo_lines = _extract_todo_lines(text, max_lines=20)
            if todo_lines:
                todo_matches += 1
                display_path = _display_path(rel_path)
                entry = _get_entry(display_path)
                entry["todo_lines"].extend(todo_lines)
                if not entry.get("context_excerpt"):
                    context_excerpt = "\n".join(text.splitlines()[:40]).strip()
                    if context_excerpt:
                        entry["context_excerpt"] = context_excerpt
        if todo_matches >= max_code_files:
            break

    requirements_by_source: List[RequirementSource] = []
    for path, entry in sorted(sources_by_path.items()):
        requirement_lines = entry.get("requirement_lines", [])
        todo_lines = entry.get("todo_lines", [])
        is_requirement_doc = bool(entry.get("is_requirement_doc"))
        if not requirement_lines and not todo_lines and not is_requirement_doc:
            continue
        requirements_text = ""
        if requirement_lines:
            requirements_text = "\n".join(f"- {line}" for line in requirement_lines)
        elif is_requirement_doc:
            requirements_text = "No explicit requirement statements found; infer requirements from the context excerpt."
        if todo_lines:
            todo_block = "\n".join(f"- {line}" for line in todo_lines)
            if requirements_text:
                requirements_text = f"{requirements_text}\n\nTODO/FIXME items:\n{todo_block}"
            else:
                requirements_text = f"TODO/FIXME items:\n{todo_block}"
        requirements_by_source.append(
            RequirementSource(
                path=path,
                requirements_text=requirements_text or "No explicit requirements found; infer from context.",
                requirement_lines=requirement_lines,
                todo_lines=todo_lines,
                context_excerpt=entry.get("context_excerpt", ""),
            )
        )

    return requirements_by_source, context_blocks


def scan_project_requirements(
    project_root: str,
    *,
    max_files: int = 60,
    max_file_bytes: int = 200_000,
    max_context_files: int = 8,
    max_code_files: int = 80,
    extra_ignored: Optional[List[str]] = None,
) -> ProjectScanResult:
    """
    Scan a project folder for requirement signals.

    Heuristics:
    - Prefer README/spec/plan/roadmap/docs paths.
    - Extract requirement-like statements (must/should/etc.).
    - Extract TODO/FIXME/BUG/XXX lines from code/text files.
    - Capture small context excerpts for prompting.
    - Keep requirements per file to avoid merging across sources.
    """
    requirements_by_source, context_blocks = _scan_requirements_root(
        project_root,
        root_display=None,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_context_files=max_context_files,
        max_code_files=max_code_files,
        extra_ignored=extra_ignored,
    )

    context_summary = "\n\n".join(context_blocks).strip()
    if not context_summary:
        context_summary = "No additional context excerpts available."

    return ProjectScanResult(
        requirements_by_source=requirements_by_source,
        sources=[item.path for item in requirements_by_source],
        context_summary=context_summary,
    )


def _strip_code_fences(text: str) -> str:
    fenced = (text or "").strip()
    if "```" in fenced:
        blocks = re.findall(r"```[a-zA-Z0-9_-]*\s*([\s\S]*?)```", fenced, flags=re.IGNORECASE)
        if blocks:
            for block in blocks:
                if "{" in block or "[" in block:
                    return block.strip()
            return blocks[0].strip()
        if fenced.startswith("```"):
            fenced = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", fenced)
    return fenced.strip()


def _strip_json_comments(text: str) -> str:
    if not text:
        return text
    out: List[str] = []
    in_string = False
    escape = False
    idx = 0
    length = len(text)
    while idx < length:
        ch = text[idx]
        if escape:
            out.append(ch)
            escape = False
            idx += 1
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            idx += 1
            continue
        if ch == '"':
            out.append(ch)
            in_string = not in_string
            idx += 1
            continue
        if not in_string and ch == "/" and idx + 1 < length:
            nxt = text[idx + 1]
            if nxt == "/":
                idx += 2
                while idx < length and text[idx] not in ("\n", "\r"):
                    idx += 1
                continue
            if nxt == "*":
                idx += 2
                while idx + 1 < length and not (text[idx] == "*" and text[idx + 1] == "/"):
                    idx += 1
                idx = idx + 2 if idx + 1 < length else length
                continue
        out.append(ch)
        idx += 1
    return "".join(out)


def _extract_json_snippet(text: str) -> str:
    if not text:
        return text
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return cleaned
    if "{" not in cleaned and "[" not in cleaned:
        return cleaned
    start_obj = cleaned.find("{")
    start_arr = cleaned.find("[")
    if start_obj == -1 and start_arr == -1:
        return cleaned
    if start_obj == -1 or (start_arr != -1 and start_arr < start_obj):
        start = start_arr
        open_ch = "["
        close_ch = "]"
    else:
        start = start_obj
        open_ch = "{"
        close_ch = "}"
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return cleaned[start : idx + 1]
    last_close = cleaned.rfind(close_ch)
    if last_close != -1 and last_close > start:
        return cleaned[start : last_close + 1]
    return cleaned[start:]


def _sanitize_json_text(text: str) -> str:
    if not text:
        return text
    out: List[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            out.append(ch)
            in_string = not in_string
            continue
        if in_string and ord(ch) < 0x20:
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(f"\\u{ord(ch):04x}")
            continue
        out.append(ch)
    return "".join(out)


def _fix_invalid_json_escapes(text: str) -> str:
    if not text:
        return text
    out: List[str] = []
    in_string = False
    escape = False
    valid_escapes = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
    length = len(text)
    for idx, ch in enumerate(text):
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            if in_string:
                nxt = text[idx + 1] if idx + 1 < length else ""
                if nxt and nxt not in valid_escapes:
                    out.append("\\\\")
                    continue
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


def _fix_incomplete_unicode_escapes(text: str) -> str:
    if not text:
        return text
    out: List[str] = []
    in_string = False
    escape = False
    idx = 0
    length = len(text)
    hex_digits = set("0123456789abcdefABCDEF")
    while idx < length:
        ch = text[idx]
        if escape:
            out.append(ch)
            escape = False
            idx += 1
            continue
        if ch == "\\":
            if in_string and idx + 1 < length and text[idx + 1] == "u":
                hex_part = text[idx + 2 : idx + 6]
                if len(hex_part) < 4 or any(c not in hex_digits for c in hex_part):
                    out.append("\\\\")
                    idx += 1
                    continue
            out.append(ch)
            escape = True
            idx += 1
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            idx += 1
            continue
        out.append(ch)
        idx += 1
    return "".join(out)


def _escape_unbalanced_quotes(text: str) -> str:
    if not text:
        return text
    out: List[str] = []
    in_string = False
    escape = False
    length = len(text)
    for idx, ch in enumerate(text):
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            if in_string:
                j = idx + 1
                while j < length and text[j].isspace():
                    j += 1
                if j < length:
                    nxt = text[j]
                    if nxt not in {",", "}", "]", ":"}:
                        out.append('\\"')
                        continue
                in_string = False
                out.append(ch)
                continue
            in_string = True
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


def _close_unterminated_string(text: str) -> str:
    if not text:
        return text
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
    if not in_string:
        return text
    cleaned = text
    if cleaned.endswith("\\") and not cleaned.endswith("\\\\"):
        cleaned = cleaned[:-1]
    return cleaned + '"'


def _balance_json_brackets(text: str) -> str:
    if not text:
        return text
    stack: List[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()
    if not stack:
        return text
    return text + "".join(reversed(stack))


def _repair_json_like(text: str) -> str:
    if not text:
        return text
    repaired = _extract_json_snippet(text)
    repaired = _strip_json_comments(repaired)
    repaired = repaired.replace("\ufeff", "")
    # Drop common placeholder ellipses outside strings.
    repaired = re.sub(r",\s*\.\.\.\s*(?=[}\]])", "", repaired)
    repaired = re.sub(r"\n\s*\.\.\.\s*\n", "\n", repaired)
    repaired = re.sub(r"\.\.\.\s*$", "", repaired)
    # Drop trailing commas before object/array close.
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    # Quote single-quoted keys: {'key': ...} -> {"key": ...}
    repaired = re.sub(r"([{\[,]\s*)'([^']+)'\s*:", r'\1"\2":', repaired)
    # Quote unquoted keys: {key: ...} -> {"key": ...}
    repaired = re.sub(r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_-]*)\s*:", r'\1"\2":', repaired)
    repaired = _fix_invalid_json_escapes(repaired)
    repaired = _fix_incomplete_unicode_escapes(repaired)
    repaired = _escape_unbalanced_quotes(repaired)
    repaired = _close_unterminated_string(repaired)
    repaired = _balance_json_brackets(repaired)
    return repaired


def _raw_decode_json_dict(text: str) -> Optional[Dict[str, object]]:
    if not text:
        return None
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_json_payload(text: str) -> Dict[str, object]:
    cleaned = _strip_code_fences(text)
    snippet = _extract_json_snippet(cleaned or "")
    raw_snippet = _extract_json_snippet(text or "")

    candidates: List[str] = []
    for candidate in (cleaned, snippet, raw_snippet):
        if candidate:
            candidates.append(candidate)
            candidates.append(_sanitize_json_text(candidate))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    for candidate in candidates:
        parsed = _raw_decode_json_dict(candidate)
        if isinstance(parsed, dict):
            return parsed

    for candidate in (snippet, raw_snippet, cleaned):
        if not candidate:
            continue
        repaired = _repair_json_like(candidate)
        for attempt in (repaired, _sanitize_json_text(repaired)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
        parsed = _raw_decode_json_dict(repaired)
        if isinstance(parsed, dict):
            return parsed
        try:
            pythonish = re.sub(r"\bnull\b", "None", repaired, flags=re.IGNORECASE)
            pythonish = re.sub(r"\btrue\b", "True", pythonish, flags=re.IGNORECASE)
            pythonish = re.sub(r"\bfalse\b", "False", pythonish, flags=re.IGNORECASE)
            parsed = ast.literal_eval(pythonish)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    logger.error("Failed to parse LLM JSON payload: no valid JSON object found.")
    raise json.JSONDecodeError("Failed to parse LLM JSON payload", cleaned or "", 0)


def _extract_schema_from_prompt(prompt: str) -> str:
    if not prompt:
        return ""
    markers = (
        "Return JSON only with this schema:",
        "Respond with JSON only. Schema:",
        "Return ONLY JSON matching the schema provided.",
    )
    for marker in markers:
        idx = prompt.find(marker)
        if idx == -1:
            continue
        schema = prompt[idx + len(marker):]
        note_idx = schema.find("\nNotes:")
        if note_idx != -1:
            schema = schema[:note_idx]
        return schema.strip()
    return ""


def _rephrase_json_payload(
    provider,
    *,
    response_text: str,
    schema_hint: str,
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
    actions_log: List[str],
    label: str,
) -> Optional[Dict[str, object]]:
    if not response_text:
        return None
    snippet = _truncate_text(response_text, 6000)
    schema_block = schema_hint or "{}"
    user_prompt = (
        "Your previous response was not valid JSON. "
        "Rephrase it as valid JSON only, matching the schema below. "
        "Do not include commentary, code fences, or extra text.\n\n"
        "Schema:\n"
        f"{schema_block}\n\n"
        "Previous response:\n"
        f"{snippet}\n"
    )
    try:
        resp = provider.predict(
            [{"role": "user", "content": user_prompt}],
            system="You are a strict JSON reformatter.",
            max_tokens=llm_max_tokens or 10240,
            temperature=min(0.1, llm_temperature),
            timeout=llm_timeout,
            reasoning_effort=llm_reasoning_effort,
        )
    except Exception as exc:
        actions_log.append(f"JSON repair request failed ({label}): {exc}")
        return None
    try:
        return _parse_json_payload(resp.text or "{}")
    except json.JSONDecodeError as exc:
        actions_log.append(f"JSON repair parse failed ({label}): {exc}")
        return None


def _coerce_plan_payload(payload: object) -> Optional[Dict[str, object]]:
    if isinstance(payload, list):
        return {"plan": payload}
    if isinstance(payload, dict):
        if isinstance(payload.get("plan"), list):
            return payload
        for key in ("steps", "actions"):
            if isinstance(payload.get(key), list):
                coerced = dict(payload)
                coerced["plan"] = payload.get(key)
                return coerced
        return payload
    return None


def _plan_has_code_changes(plan_steps: List[Dict[str, object]]) -> bool:
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        step_type = _normalize_step_type(step.get("type"))
        if step_type not in {"write_file", "append_file", "replace_in_file"}:
            continue
        path = step.get("path")
        if not isinstance(path, str):
            continue
        _, ext = os.path.splitext(path)
        if ext.lower() in CODE_FILE_EXTS:
            return True
    return False


def _plan_has_test_changes(plan_steps: List[Dict[str, object]]) -> bool:
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        step_type = _normalize_step_type(step.get("type"))
        if step_type not in {"write_file", "append_file", "replace_in_file"}:
            continue
        path = step.get("path")
        if isinstance(path, str) and TEST_FILE_RE.search(path):
            return True
    return False


def _collect_string_fields(value: object, collected: List[str]) -> None:
    if isinstance(value, str):
        collected.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_string_fields(item, collected)
        return
    if isinstance(value, list):
        for item in value:
            _collect_string_fields(item, collected)


def _parse_opencode_output(stdout: str) -> Optional[Dict[str, object]]:
    if not stdout:
        return None
    try:
        return _parse_json_payload(stdout)
    except json.JSONDecodeError:
        pass

    candidates: List[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        _collect_string_fields(obj, candidates)

    for candidate in candidates:
        try:
            payload = _parse_json_payload(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _verification_output_issue(
    stdout: str,
    stderr: str,
    dataset_summary: Optional[Dict[str, object]] = None,
    eval_info: Optional[Dict[str, object]] = None,
) -> Optional[str]:
    output = f"{stdout}\n{stderr}"
    if FUTURE_WARNING_RE.search(output):
        return "future warning detected"
    if NO_TESTS_RAN_RE.search(output):
        return "no tests ran"
    mod_match = MODULE_NOT_FOUND_RE.search(output)
    if mod_match:
        return f"missing module: {mod_match.group(1)}"
    if NO_DATA_PROCESSED_RE.search(output):
        return "no documents processed"
    if ACCURACY_TARGET_RE.search(output):
        return "accuracy target not met"
    if eval_info:
        label_values = eval_info.get("label_values")
        if isinstance(label_values, list) and label_values:
            labels = {val.lower() for val in label_values if isinstance(val, str)}
            predicted_lines = []
            for line in output.splitlines():
                lower = line.lower()
                if "predicted" in lower and (":" in line or "=" in line):
                    predicted_lines.append(line)
            if predicted_lines:
                non_normalized = 0
                total = 0
                for line in predicted_lines:
                    total += 1
                    chunk = line.split(":", 1)[-1] if ":" in line else line.split("=", 1)[-1]
                    chunk = chunk.split("|", 1)[0].strip().strip("'\"").lower()
                    if chunk not in labels:
                        non_normalized += 1
                if total >= 3 and non_normalized / total >= 0.6:
                    return "predicted labels not normalised to eval_data values"
    if dataset_summary:
        data_count = int(dataset_summary.get("count") or 0)
        if data_count > 1:
            lines = output.splitlines()
            processing_lines = [
                line for line in lines if "processing" in line.lower() and ".pdf" in line.lower()
            ]
            default_lines = [
                line for line in lines if DEFAULT_FALLBACK_RE.search(line) or EMPTY_OR_MALFORMED_RE.search(line)
            ]
            mentioned = set()
            for name in dataset_summary.get("files") or []:
                if isinstance(name, str) and name.lower() in output.lower():
                    mentioned.add(name)
            observed = max(len(processing_lines), len(mentioned))
            if observed == 0:
                if "designed to be imported" in output.lower():
                    return "solution script not runnable (import-only)"
                return "no dataset processing output"
            if observed > 0 and observed < data_count:
                return f"partial dataset processed ({observed}/{data_count})"
            if default_lines and len(default_lines) >= max(1, data_count // 2):
                return f"defaults returned for many files ({len(default_lines)}/{data_count})"
            pages = PREDICTED_PAGE_RE.findall(output)
            if pages and len(set(pages)) == 1 and len(pages) >= max(2, data_count // 2):
                return "constant predicted page"
            rotations = PREDICTED_ROT_RE.findall(output)
            if rotations and len(set(rotations)) == 1 and len(rotations) >= max(2, data_count // 2):
                return "constant predicted rotation"
            labels = [label.strip().lower() for label in PREDICTED_LABEL_RE.findall(output)]
            if labels and len(set(labels)) == 1 and len(labels) >= max(2, data_count // 2):
                return "constant predicted label"
            coords = []
            for lat, lon in PREDICTED_COORD_RE.findall(output):
                try:
                    coords.append((round(float(lat), 3), round(float(lon), 3)))
                except Exception:
                    continue
            if coords and len(set(coords)) == 1 and len(coords) >= max(2, data_count // 2):
                return "constant predicted centroid"
            if not lines or not any(line.strip() for line in lines):
                return "no output produced"
    if VERIFICATION_ERROR_RE.search(output):
        return "error output detected"
    return None


def _is_verification_command(command: str) -> bool:
    if not command:
        return False
    cmd = command.lower()
    test_patterns = (
        r"\bpytest\b",
        r"\bpython(?:3)?\s+-m\s+pytest\b",
        r"\bcoverage\s+run\b.*\bpytest\b",
        r"\bpython(?:3)?\s+-m\s+unittest\b",
        r"\bnosetests?\b",
        r"\btox\b",
        r"\bnox\b",
        r"\bhatch\s+test\b",
        r"\bhatch\s+run\s+test\b",
        r"\bpoetry\s+run\s+pytest\b",
        r"\bpdm\s+run\s+pytest\b",
        r"\bpipenv\s+run\s+pytest\b",
        r"\buv\s+run\s+pytest\b",
        r"\buv\s+run\s+python(?:3)?\s+-m\s+pytest\b",
        r"\buv\s+run\s+python(?:3)?\s+-m\s+unittest\b",
        r"\buv\s+run\s+.*\.py\b",
        r"\buvx\s+pytest\b",
        r"\buvx\s+python(?:3)?\s+-m\s+pytest\b",
        r"\buvx\s+python(?:3)?\s+-m\s+unittest\b",
        r"\bgo\s+test\b",
        r"\bcargo\s+test\b",
        r"\bcargo\s+nextest\b",
        r"\bctest\b",
        r"\bmeson\s+test\b",
        r"\bdotnet\s+test\b",
        r"\bmvn\s+test\b",
        r"\bgradle\s+test\b",
        r"\bgradle\s+check\b",
        r"\b\./gradlew\s+test\b",
        r"\bmake\s+(test|check)\b",
        r"\bninja\s+test\b",
        r"\bflutter\s+test\b",
        r"\bdart\s+test\b",
        r"\bdeno\s+test\b",
        r"\bnpm\s+(test|run\s+test)\b",
        r"\byarn\s+test\b",
        r"\bpnpm\s+(test|run\s+test)\b",
        r"\bbun\s+test\b",
        r"\bvitest\b",
        r"\bjest\b",
        r"\bmocha\b",
        r"\bava\b",
        r"\btap\b",
        r"\btape\b",
        r"\bplaywright\s+test\b",
        r"\bcypress\s+(run|open)\b",
        r"\bkarma\s+start\b",
        r"\bng\s+test\b",
        r"\bnx\s+test\b",
        r"\blerna\s+run\s+test\b",
        r"\bturbo\s+run\s+test\b",
        r"\bbundle\s+exec\s+rspec\b",
        r"\brspec\b",
        r"\brake\s+test\b",
        r"\bminitest\b",
        r"\bphpunit\b",
        r"\bvendor/bin/phpunit\b",
        r"\bpest\b",
        r"\bmix\s+test\b",
        r"\brebar3\s+(eunit|ct)\b",
        r"\bstack\s+test\b",
        r"\bcabal\s+test\b",
        r"\bswift\s+test\b",
        r"\bxcodebuild\s+test\b",
    )
    if any(re.search(pattern, cmd) for pattern in test_patterns):
        return True
    if "python" in cmd and ".py" in cmd and "pip" not in cmd:
        return True
    if "python" in cmd and " -m " in cmd and "pip" not in cmd and "venv" not in cmd and "ensurepip" not in cmd:
        return True
    if cmd.startswith("./") or "npm run" in cmd or "yarn run" in cmd or "pnpm run" in cmd:
        return True
    return False


def _plan_has_verification(plan_steps: List[Dict[str, object]]) -> bool:
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        step_type = _normalize_step_type(step.get("type"))
        if step_type != "run_command":
            continue
        command = step.get("command")
        if isinstance(command, str) and _is_verification_command(command):
            return True
    return False


def _split_verification_steps(plan_steps: List[Dict[str, object]]) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    non_verification: List[Dict[str, object]] = []
    verification: List[Dict[str, object]] = []
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        step_type = _normalize_step_type(step.get("type"))
        if step_type == "run_command":
            command = step.get("command")
            if isinstance(command, str) and _is_verification_command(command):
                verification.append(step)
                continue
        non_verification.append(step)
    return non_verification, verification


def _source_requires_code(source: RequirementSource) -> bool:
    _, ext = os.path.splitext(source.path or "")
    if ext.lower() in CODE_FILE_EXTS:
        return True
    if source.requirement_lines:
        combined = " ".join(source.requirement_lines)
        if CODE_REQUIREMENT_RE.search(combined):
            return True
    if source.requirements_text and CODE_REQUIREMENT_RE.search(source.requirements_text):
        return True
    return False


def _source_is_pure_code_request(source: RequirementSource) -> bool:
    if not _source_requires_code(source):
        return False
    combined = " ".join(
        item for item in [source.requirements_text, " ".join(source.requirement_lines), " ".join(source.todo_lines)] if item
    )
    if not combined:
        return False
    return not NON_CODE_HINT_RE.search(combined)


def _extract_source_requirements(source: RequirementSource, max_fallback: int = 10) -> List[str]:
    requirements: List[str] = []
    for item in source.requirement_lines + source.todo_lines:
        cleaned = item.strip()
        if cleaned:
            requirements.append(cleaned)
    if not requirements and source.requirements_text:
        for line in source.requirements_text.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("#"):
                continue
            requirements.append(cleaned)
            if len(requirements) >= max_fallback:
                break
    seen = set()
    unique = []
    for req in requirements:
        if req not in seen:
            seen.add(req)
            unique.append(req)
    return unique


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_codingagent(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    norm = value.strip().lower()
    if norm in {"opencode", "codex", "llm"}:
        return norm
    return None


def _normalize_reasoning_effort(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned.lower()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    cleaned = str(raw).strip().lower()
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    return default


class _FallbackProvider:
    def __init__(self, primary, fallback, actions_log: List[str]):
        self._primary = primary
        self._fallback = fallback
        self._actions_log = actions_log
        self._quota_exhausted = False

    def __getattr__(self, name: str):
        return getattr(self._primary, name)

    @property
    def model(self):
        return getattr(self._primary, "model", None)

    @model.setter
    def model(self, value):
        if hasattr(self._primary, "model"):
            try:
                self._primary.model = value
            except Exception:
                pass

    def predict(
        self,
        messages: List[Dict[str, object]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ):
        if self._quota_exhausted and self._fallback:
            return self._fallback.predict(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
        try:
            return self._primary.predict(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
        except LLMQuotaError as exc:
            retries = int(os.getenv("LLM_RATE_LIMIT_RETRIES", "2"))
            base = float(os.getenv("LLM_RATE_LIMIT_BACKOFF_BASE", "1.0"))
            for attempt in range(retries):
                delay = max(0.1, base * (2 ** attempt))
                self._actions_log.append(
                    f"Rate limit encountered; backing off {delay:.2f}s before retry {attempt + 1}/{retries}."
                )
                time.sleep(delay)
                try:
                    return self._primary.predict(
                        messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        timeout=timeout,
                        reasoning_effort=reasoning_effort,
                    )
                except LLMQuotaError:
                    continue
            if not self._fallback:
                raise
            self._quota_exhausted = True
            primary_name = getattr(self._primary, "name", "primary")
            fallback_name = getattr(self._fallback, "name", "fallback")
            self._actions_log.append(
                f"LLM quota error from {primary_name}; switching to fallback {fallback_name} after retries."
            )
            return self._fallback.predict(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
        except LLMError as exc:
            msg = str(exc).lower()
            if "timed out" in msg or "timeout" in msg:
                retries = int(os.getenv("LLM_TIMEOUT_RETRIES", "1"))
                base = float(os.getenv("LLM_TIMEOUT_BACKOFF_BASE", "1.0"))
                for attempt in range(retries):
                    delay = max(0.1, base * (2 ** attempt))
                    self._actions_log.append(
                        f"Timeout encountered; backing off {delay:.2f}s before retry {attempt + 1}/{retries}."
                    )
                    time.sleep(delay)
                    try:
                        return self._primary.predict(
                            messages,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            system=system,
                            timeout=timeout,
                            reasoning_effort=reasoning_effort,
                        )
                    except LLMError as retry_exc:
                        retry_msg = str(retry_exc).lower()
                        if "timed out" in retry_msg or "timeout" in retry_msg:
                            continue
                        raise
                if self._fallback:
                    primary_name = getattr(self._primary, "name", "primary")
                    fallback_name = getattr(self._fallback, "name", "fallback")
                    self._actions_log.append(
                        f"Timeouts from {primary_name}; using fallback {fallback_name} for this call."
                    )
                    return self._fallback.predict(
                        messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        timeout=timeout,
                        reasoning_effort=reasoning_effort,
                    )
            if self._fallback and ("empty text" in msg or "incomplete" in msg):
                primary_name = getattr(self._primary, "name", "primary")
                fallback_name = getattr(self._fallback, "name", "fallback")
                self._actions_log.append(
                    f"{primary_name} returned empty/incomplete output; using fallback {fallback_name} for this call."
                )
                return self._fallback.predict(
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    timeout=timeout,
                    reasoning_effort=reasoning_effort,
                )
            raise

    def cleanup(self) -> None:
        for prov in (self._primary, self._fallback):
            if prov is None:
                continue
            cleanup = getattr(prov, "cleanup", None)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception:
                    pass


def _coerce_list(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = []
        for item in value:
            cleaned = _safe_str(item)
            if cleaned:
                items.append(cleaned)
        return items
    cleaned = _safe_str(value)
    return [cleaned] if cleaned else []


def _normalize_requirement_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "unspecified"
    if raw in ("functional", "func"):
        return "functional"
    if "non" in raw and "functional" in raw:
        return "non-functional"
    if raw in ("nfr", "quality", "quality-attribute"):
        return "non-functional"
    if "constraint" in raw:
        return "constraint"
    if "assumption" in raw:
        return "assumption"
    return raw


def _normalize_priority(value: Optional[str], text: str = "") -> str:
    raw = (value or "").strip().lower()
    text_lower = (text or "").lower()
    must_terms = ("must", "shall", "required", "mandatory")
    should_terms = ("should",)
    could_terms = ("could", "may", "optional")
    if raw in ("must", "high", "critical", "p0", "p1"):
        return "must"
    if raw in ("should", "medium", "p2"):
        return "should"
    if raw in ("could", "low", "p3", "p4"):
        return "could"
    if raw in ("wont", "won't", "not"):
        return "wont"
    if any(term in text_lower for term in must_terms):
        return "must"
    if any(term in text_lower for term in should_terms):
        return "should"
    if any(term in text_lower for term in could_terms):
        return "could"
    return "should" if text_lower else "unspecified"


def _normalize_requirement_id(raw: object, used: set, next_id: int) -> Tuple[str, int]:
    if isinstance(raw, str):
        match = REQ_ID_RE.search(raw.upper())
        if match:
            req_id = match.group(0)
            if req_id not in used:
                used.add(req_id)
                return req_id, next_id
    while True:
        req_id = f"REQ-{next_id:03d}"
        next_id += 1
        if req_id not in used:
            used.add(req_id)
            return req_id, next_id


def _truncate_text(text: str, max_chars: int) -> str:
    cleaned = text or ""
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "...(truncated)"


def _build_requirements_corpus(
    requirement_sources: List[RequirementSource],
    context_summary: str,
    *,
    max_chars: int = 12000,
    per_source_chars: int = 1200,
    context_chars: int = 2000,
) -> str:
    parts: List[str] = []
    total = 0
    for source in requirement_sources:
        req_text = (source.requirements_text or "").strip()
        ctx_text = (source.context_excerpt or "").strip()
        if not req_text and not ctx_text:
            continue
        block = f"Source: {source.path}\nRequirements:\n{_truncate_text(req_text, per_source_chars)}"
        if ctx_text:
            block += f"\nContext:\n{_truncate_text(ctx_text, max(200, per_source_chars // 2))}"
        block += "\n"
        if total + len(block) > max_chars:
            parts.append("... (requirements corpus truncated)")
            break
        parts.append(block)
        total += len(block)
    if context_summary:
        summary_block = "Context summary:\n" + _truncate_text(context_summary.strip(), context_chars)
        parts.append(summary_block)
    if not parts:
        return "No explicit requirements were found."
    return "\n".join(parts).strip()


def _normalize_requirements_register(
    payload: Dict[str, object],
    requirement_sources: List[RequirementSource],
) -> Dict[str, object]:
    raw_requirements = payload.get("requirements") if isinstance(payload, dict) else []
    if not isinstance(raw_requirements, list):
        raw_requirements = []
    default_sources = sorted({src.path for src in requirement_sources}) if requirement_sources else []
    normalized: List[Dict[str, object]] = []
    used_ids: set = set()
    next_id = 1
    for item in raw_requirements:
        if isinstance(item, str):
            item = {"description": item}
        if not isinstance(item, dict):
            continue
        req_id, next_id = _normalize_requirement_id(item.get("id"), used_ids, next_id)
        description = _safe_str(item.get("description") or item.get("requirement") or item.get("detail"))
        title = _safe_str(item.get("title") or item.get("name") or description)
        if not description:
            description = title
        if not title:
            title = f"Requirement {req_id}"
        req_type = _normalize_requirement_type(_safe_str(item.get("type")))
        priority = _normalize_priority(_safe_str(item.get("priority")), f"{title} {description}")
        sources = _coerce_list(item.get("source") or item.get("sources"))
        if not sources and default_sources:
            sources = list(default_sources)
        acceptance = _coerce_list(
            item.get("acceptance_criteria")
            or item.get("acceptanceCriteria")
            or item.get("acceptance")
        )
        dependencies = _coerce_list(item.get("dependencies") or item.get("depends_on"))
        dep_ids: List[str] = []
        for dep in dependencies:
            match = REQ_ID_RE.search(dep.upper())
            dep_ids.append(match.group(0) if match else dep)
        verification = _safe_str(item.get("verification") or item.get("validation") or item.get("test"))
        rationale = _safe_str(item.get("rationale") or item.get("reason"))
        notes = _safe_str(item.get("notes"))
        normalized.append(
            {
                "id": req_id,
                "title": title,
                "description": description,
                "type": req_type,
                "priority": priority,
                "source": sources,
                "acceptance_criteria": acceptance,
                "dependencies": dep_ids,
                "verification": verification,
                "rationale": rationale,
                "notes": notes,
            }
        )
    assumptions = _coerce_list(payload.get("assumptions") if isinstance(payload, dict) else None)
    open_questions = _coerce_list(
        payload.get("open_questions") if isinstance(payload, dict) else None
    )
    return {
        "requirements": normalized,
        "assumptions": assumptions,
        "open_questions": open_questions,
    }


def _ensure_global_requirements(
    register: Dict[str, object],
    *,
    requirement_sources: List[RequirementSource],
) -> Dict[str, object]:
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    used_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements
        if isinstance(item, dict) and _safe_str(item.get("id"))
    }
    default_sources = sorted({src.path for src in requirement_sources}) if requirement_sources else []
    existing_titles = {
        _safe_str(item.get("title")).lower()
        for item in requirements
        if isinstance(item, dict)
    }
    existing_descriptions = {
        _safe_str(item.get("description")).lower()
        for item in requirements
        if isinstance(item, dict)
    }
    next_id = 1
    for req in GLOBAL_REQUIREMENTS:
        title = _safe_str(req.get("title"))
        desc = _safe_str(req.get("description"))
        if title.lower() in existing_titles or desc.lower() in existing_descriptions:
            continue
        req_id, next_id = _normalize_requirement_id(req.get("id"), used_ids, next_id)
        requirements.append(
            {
                "id": req_id,
                "title": title,
                "description": desc,
                "type": _normalize_requirement_type(_safe_str(req.get("type"))),
                "priority": _normalize_priority(_safe_str(req.get("priority")), f"{title} {desc}"),
                "source": ["global"] if not default_sources else ["global"] + default_sources,
                "acceptance_criteria": list(req.get("acceptance_criteria") or []),
                "dependencies": list(req.get("dependencies") or []),
                "verification": _safe_str(req.get("verification")),
                "rationale": _safe_str(req.get("rationale")),
                "notes": _safe_str(req.get("notes")),
            }
        )
        existing_titles.add(title.lower())
        existing_descriptions.add(desc.lower())
    register["requirements"] = requirements
    return register


def _fallback_requirements_register(
    requirement_sources: List[RequirementSource],
    context_summary: str,
) -> Dict[str, object]:
    items: List[Dict[str, object]] = []
    seen: set = set()
    used_ids: set = set()
    next_id = 1
    for source in requirement_sources:
        for statement in _extract_source_requirements(source, max_fallback=20):
            normalized = re.sub(r"\s+", " ", statement.lower()).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            req_id, next_id = _normalize_requirement_id(None, used_ids, next_id)
            items.append(
                {
                    "id": req_id,
                    "title": statement[:80],
                    "description": statement,
                    "type": "unspecified",
                    "priority": _normalize_priority(None, statement),
                    "source": [source.path],
                    "acceptance_criteria": [],
                    "dependencies": [],
                    "verification": "",
                    "rationale": "",
                    "notes": "Generated from raw requirement statements.",
                }
            )
    if not items:
        req_id, _ = _normalize_requirement_id(None, used_ids, 1)
        description = "Infer missing requirements from project context and clarify with stakeholders."
        if context_summary:
            description = f"Infer requirements from context: {_truncate_text(context_summary, 200)}"
        items.append(
            {
                "id": req_id,
                "title": "Infer requirements from context",
                "description": description,
                "type": "unspecified",
                "priority": "should",
                "source": [],
                "acceptance_criteria": [],
                "dependencies": [],
                "verification": "",
                "rationale": "",
                "notes": "Fallback requirement because no explicit statements were found.",
            }
        )
    return {"requirements": items, "assumptions": [], "open_questions": []}


def _detect_sequence_gaps(
    project_root: str,
    *,
    extra_ignored: Optional[List[str]] = None,
    max_paths_per_prefix: int = 5,
) -> List[Dict[str, object]]:
    ignored = _ignored_dirnames(extra_ignored)
    prefixes: Dict[str, Dict[str, object]] = {}
    for walk_root, dirs, files in os.walk(project_root):
        _filter_walk_dirs(walk_root, dirs, ignored)
        for filename in files:
            stem, _ = os.path.splitext(filename)
            match = SEQ_NAME_RE.match(stem)
            if not match:
                continue
            prefix = match.group("prefix")
            try:
                num = int(match.group("num"))
            except ValueError:
                continue
            entry = prefixes.setdefault(prefix, {"numbers": set(), "paths": []})
            entry["numbers"].add(num)
            if len(entry["paths"]) < max_paths_per_prefix:
                entry["paths"].append(os.path.relpath(os.path.join(walk_root, filename), project_root))

    gaps: List[Dict[str, object]] = []
    for prefix, entry in prefixes.items():
        nums = sorted(entry.get("numbers") or [])
        if len(nums) < 2:
            continue
        missing = [n for n in range(nums[0], nums[-1] + 1) if n not in entry["numbers"]]
        if missing:
            gaps.append(
                {
                    "prefix": prefix,
                    "observed": nums,
                    "missing": missing,
                    "examples": entry.get("paths") or [],
                }
            )
    return gaps


def _ensure_sequence_requirements(
    register: Dict[str, object],
    sequence_gaps: List[Dict[str, object]],
    *,
    requirement_sources: List[RequirementSource],
) -> Dict[str, object]:
    if not sequence_gaps:
        return register
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    used_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements
        if isinstance(item, dict) and _safe_str(item.get("id"))
    }
    existing_desc = {
        _safe_str(item.get("description")).lower()
        for item in requirements
        if isinstance(item, dict)
    }
    default_sources = sorted({src.path for src in requirement_sources}) if requirement_sources else []
    next_id = 1
    for gap in sequence_gaps:
        prefix = _safe_str(gap.get("prefix"))
        missing = gap.get("missing") if isinstance(gap.get("missing"), list) else []
        observed = gap.get("observed") if isinstance(gap.get("observed"), list) else []
        if not prefix or not missing:
            continue
        range_min = min(observed) if observed else min(missing)
        range_max = max(observed) if observed else max(missing)
        missing_labels = ", ".join(f"{prefix}{num}" for num in missing)
        description = (
            f"Ensure sequence completeness for '{prefix}' items. "
            f"Missing identifiers: {missing_labels}. "
            "Create the missing items or document a justified omission."
        )
        if description.lower() in existing_desc:
            continue
        req_id, next_id = _normalize_requirement_id(None, used_ids, next_id)
        requirements.append(
            {
                "id": req_id,
                "title": f"Complete {prefix} sequence",
                "description": description,
                "type": "constraint",
                "priority": "must",
                "source": ["sequence_check"] + (default_sources or []),
                "acceptance_criteria": [
                    f"All {prefix} items between {range_min} and {range_max} exist or have a documented omission.",
                    "Any gaps are resolved or explicitly justified in documentation.",
                ],
                "dependencies": [],
                "verification": f"Check for missing {prefix} sequence items and document resolutions.",
                "rationale": "Sequence gaps often indicate incomplete work or missing tests.",
                "notes": "Auto-generated from sequence gap detection.",
            }
        )
        existing_desc.add(description.lower())
    register["requirements"] = requirements
    return register


def _ensure_dataset_requirements(
    register: Dict[str, object],
    dataset_summary: Dict[str, object],
    requirements_material: str,
    *,
    requirement_sources: List[RequirementSource],
) -> Dict[str, object]:
    count = int(dataset_summary.get("count") or 0)
    if count <= 1:
        return register
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    used_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements
        if isinstance(item, dict) and _safe_str(item.get("id"))
    }
    existing_desc = {
        _safe_str(item.get("description")).lower()
        for item in requirements
        if isinstance(item, dict)
    }
    default_sources = sorted({src.path for src in requirement_sources}) if requirement_sources else []
    must_re = re.compile(r"\b(each|every|all)\b", re.IGNORECASE)
    priority = "must" if must_re.search(requirements_material or "") else "should"
    description = (
        f"Process all available sample data files in the project data/ folder "
        f"({count} files detected) unless requirements explicitly state otherwise."
    )
    if description.lower() in existing_desc:
        return register
    req_id, _ = _normalize_requirement_id(None, used_ids, 1)
    requirements.append(
        {
            "id": req_id,
            "title": "Process all sample data files",
            "description": description,
            "type": "functional",
            "priority": priority,
            "source": ["dataset_check"] + (default_sources or []),
            "acceptance_criteria": [
                "Each available data file is processed or explicitly excluded with justification.",
                "Summary output reports the number of files processed.",
            ],
            "dependencies": [],
            "verification": "Run the solution over the data/ folder and confirm per-file output coverage.",
            "rationale": "Ensures solutions generalize beyond a single sample file.",
            "notes": "Auto-generated from dataset detection.",
        }
    )
    register["requirements"] = requirements
    return register


def _ensure_accuracy_strategy_requirements(
    register: Dict[str, object],
    requirements_material: str,
    *,
    requirement_sources: List[RequirementSource],
) -> Dict[str, object]:
    if not requirements_material:
        return register
    if not re.search(r"\b(accuracy|precis|recall|f1|classif|predict|prediction|label|mismatch)\b", requirements_material, re.IGNORECASE):
        return register
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    used_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements
        if isinstance(item, dict) and _safe_str(item.get("id"))
    }
    existing_desc = {
        _safe_str(item.get("description")).lower()
        for item in requirements
        if isinstance(item, dict)
    }
    default_sources = sorted({src.path for src in requirement_sources}) if requirement_sources else []
    description = (
        "When accuracy or prediction quality is a requirement, implement a deterministic baseline heuristic, "
        "an optional advanced mode for higher accuracy (CLI flag or configuration), and normalised outputs. "
        "Use staged heuristics or caching before any expensive calls."
    )
    if description.lower() in existing_desc:
        return register
    req_id, _ = _normalize_requirement_id(None, used_ids, 1)
    requirements.append(
        {
            "id": req_id,
            "title": "Baseline + advanced accuracy strategy",
            "description": description,
            "type": "non-functional",
            "priority": "should",
            "source": ["accuracy_strategy"] + (default_sources or []),
            "acceptance_criteria": [
                "A deterministic baseline is implemented and used by default.",
                "An optional advanced mode is available to improve accuracy.",
                "Outputs are normalised to expected labels/units.",
            ],
            "dependencies": [],
            "verification": "Run baseline and advanced modes; compare accuracy outputs and ensure labels match expected schema.",
            "rationale": "Improves reliability and enables incremental accuracy gains.",
            "notes": "Auto-generated from accuracy/prediction requirements.",
        }
    )
    register["requirements"] = requirements
    return register


def _ensure_eval_schema_requirements(
    register: Dict[str, object],
    eval_info: Dict[str, object],
    *,
    requirement_sources: List[RequirementSource],
) -> Dict[str, object]:
    keys = eval_info.get("keys") if isinstance(eval_info, dict) else None
    if not keys:
        return register
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    used_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements
        if isinstance(item, dict) and _safe_str(item.get("id"))
    }
    existing_desc = {
        _safe_str(item.get("description")).lower()
        for item in requirements
        if isinstance(item, dict)
    }
    default_sources = sorted({src.path for src in requirement_sources}) if requirement_sources else []
    keys_list = ", ".join(keys)
    description = (
        "When consuming eval_data.json, treat each record as a dict and use the schema keys "
        f"[{keys_list}] as provided; do not assume dataclass-style attribute access."
    )
    if description.lower() in existing_desc:
        return register
    req_id, _ = _normalize_requirement_id(None, used_ids, 1)
    requirements.append(
        {
            "id": req_id,
            "title": "Match eval_data.json schema",
            "description": description,
            "type": "constraint",
            "priority": "must",
            "source": ["eval_data"] + (default_sources or []),
            "acceptance_criteria": [
                "Code accesses eval_data entries via dict keys matching the schema.",
                "No attribute access is used for eval_data records.",
            ],
            "dependencies": [],
            "verification": "Run evaluation using eval_data.json and confirm no schema errors occur.",
            "rationale": "Prevents runtime errors from incorrect data access patterns.",
            "notes": "Auto-generated from eval_data.json schema.",
        }
    )
    register["requirements"] = requirements
    return register


def _ensure_helper_module_requirements(
    register: Dict[str, object],
    helper_modules: Dict[str, str],
    *,
    requirement_sources: List[RequirementSource],
) -> Dict[str, object]:
    if not helper_modules:
        return register
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    used_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements
        if isinstance(item, dict) and _safe_str(item.get("id"))
    }
    existing_desc = {
        _safe_str(item.get("description")).lower()
        for item in requirements
        if isinstance(item, dict)
    }
    default_sources = sorted({src.path for src in requirement_sources}) if requirement_sources else []
    helpers_list = ", ".join(sorted(helper_modules.keys()))
    description = (
        "Prefer using existing helper modules within the project instead of "
        "introducing new dependencies or reimplementing functionality. "
        f"Detected helpers: {helpers_list}."
    )
    if description.lower() in existing_desc:
        return register
    req_id, _ = _normalize_requirement_id(None, used_ids, 1)
    requirements.append(
        {
            "id": req_id,
            "title": "Reuse existing helper modules",
            "description": description,
            "type": "constraint",
            "priority": "must",
            "source": ["helper_modules"] + (default_sources or []),
            "acceptance_criteria": [
                "Helper modules are reused where appropriate.",
                "New dependencies are added only when strictly necessary.",
            ],
            "dependencies": [],
            "verification": "Confirm solution imports and uses helper modules where relevant.",
            "rationale": "Improves consistency and avoids unnecessary dependencies.",
            "notes": "Auto-generated from helper module detection.",
        }
    )
    register["requirements"] = requirements
    return register


def _escape_md_cell(text: str) -> str:
    cleaned = text or ""
    cleaned = cleaned.replace("|", "\\|")
    return cleaned.replace("\n", "<br>")


def _format_requirements_register_markdown(register: Dict[str, object], max_items: int = 200) -> str:
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    lines = [
        "| ID | Title | Type | Priority | Description | Acceptance Criteria | Source |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for idx, req in enumerate(requirements):
        if max_items and idx >= max_items:
            lines.append("| ... | ... | ... | ... | ... | ... | ... |")
            break
        if not isinstance(req, dict):
            continue
        req_id = _escape_md_cell(_safe_str(req.get("id")))
        title = _escape_md_cell(_safe_str(req.get("title")))
        req_type = _escape_md_cell(_safe_str(req.get("type")))
        priority = _escape_md_cell(_safe_str(req.get("priority")))
        desc = _escape_md_cell(_safe_str(req.get("description")))
        acceptance = req.get("acceptance_criteria")
        acc_text = ""
        if isinstance(acceptance, list):
            acc_text = "<br>".join(_escape_md_cell(_safe_str(item)) for item in acceptance if _safe_str(item))
        else:
            acc_text = _escape_md_cell(_safe_str(acceptance))
        sources = req.get("source")
        if isinstance(sources, list):
            src_text = ", ".join(_escape_md_cell(_safe_str(item)) for item in sources if _safe_str(item))
        else:
            src_text = _escape_md_cell(_safe_str(sources))
        lines.append(f"| {req_id} | {title} | {req_type} | {priority} | {desc} | {acc_text} | {src_text} |")
    return "\n".join(lines)


def _format_requirements_register_index(
    requirements: List[Dict[str, object]],
    *,
    max_chars: int = 4000,
) -> str:
    if not requirements:
        return "No formal requirements registered."
    lines: List[str] = []
    total = 0
    for req in requirements:
        if not isinstance(req, dict):
            continue
        req_id = _safe_str(req.get("id"))
        title = _safe_str(req.get("title"))
        priority = _safe_str(req.get("priority"))
        req_type = _safe_str(req.get("type"))
        line = f"{req_id}: {title} [priority={priority}, type={req_type}]"
        if total + len(line) + 1 > max_chars:
            lines.append("... (truncated)")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _requirements_for_source(
    requirements: List[Dict[str, object]],
    source_path: str,
    *,
    max_items: int = 12,
) -> List[Dict[str, object]]:
    matches: List[Dict[str, object]] = []
    for req in requirements:
        sources = req.get("source")
        if not sources:
            continue
        if isinstance(sources, str):
            sources = [sources]
        if not isinstance(sources, list):
            continue
        for src in sources:
            if not isinstance(src, str):
                continue
            if src == source_path or src.endswith(source_path):
                matches.append(req)
                break
        if max_items and len(matches) >= max_items:
            break
    return matches


def _required_ids_for_source(requirements: List[Dict[str, object]], source_path: str) -> set:
    if not requirements:
        return set()
    matches = _requirements_for_source(requirements, source_path, max_items=0)
    ids = {
        _safe_str(req.get("id")).upper()
        for req in matches
        if isinstance(req, dict) and _safe_str(req.get("id"))
    }
    return {req_id for req_id in ids if req_id}


def _build_audit_requirements_section(
    register: Dict[str, object],
    source_path: str,
    *,
    max_items: int = 12,
) -> str:
    requirements = register.get("requirements") if isinstance(register, dict) else []
    if not isinstance(requirements, list):
        requirements = []
    lines = ["Audit requirements context:"]
    global_ids = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        sources = req.get("source")
        if isinstance(sources, str):
            sources = [sources]
        if isinstance(sources, list) and "global" in [s for s in sources if isinstance(s, str)]:
            req_id = _safe_str(req.get("id"))
            if req_id:
                global_ids.append(req_id)
    if global_ids:
        lines.append("Global requirements (must remain satisfied):")
        for req_id in global_ids:
            lines.append(f"- {req_id}")
    relevant = _requirements_for_source(requirements, source_path, max_items=max_items)
    if relevant:
        lines.append("Source-specific requirements:")
        for req in relevant:
            req_id = _safe_str(req.get("id"))
            title = _safe_str(req.get("title"))
            priority = _safe_str(req.get("priority"))
            req_type = _safe_str(req.get("type"))
            label = f"{req_id}: {title}" if req_id and title else title or req_id or "Requirement"
            meta = []
            if priority:
                meta.append(f"priority={priority}")
            if req_type:
                meta.append(f"type={req_type}")
            meta_text = f" ({', '.join(meta)})" if meta else ""
            lines.append(f"- {label}{meta_text}")
    else:
        lines.append("No source-specific requirements found.")
    return "\n".join(lines).strip()


def _select_audit_agent(
    agent_mode: str,
    *,
    source_requires_code: bool,
    codingagent_primary: Optional[str],
) -> str:
    mode = (agent_mode or "auto").strip().lower()
    if mode in {"llm", "codingagent"}:
        return mode
    if source_requires_code and codingagent_primary and codingagent_primary != "llm":
        return "codingagent"
    return "llm"


def _plan_has_audit_marker(plan_steps: List[Dict[str, object]]) -> bool:
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        for key in ("step", "note", "content"):
            val = step.get(key)
            if isinstance(val, str) and "AUDIT" in val.upper():
                return True
    return False


def _format_audit_report(payload: Dict[str, object], *, max_chars: int = 10240) -> str:
    if not isinstance(payload, dict):
        return ""
    lines: List[str] = []
    status = _safe_str(payload.get("status"))
    if status:
        lines.append(f"Status: {status}")
    summary = _safe_str(payload.get("summary"))
    if summary:
        lines.append(f"Summary: {summary}")
    for key, label in (
        ("missing_requirements", "Missing requirements"),
        ("scope_creep", "Scope drift"),
        ("risks", "Risks"),
        ("recommendations", "Recommendations"),
        ("realign_steps", "Realign steps"),
    ):
        items = payload.get(key)
        if isinstance(items, list) and items:
            lines.append(f"{label}:")
            for item in items[:10]:
                cleaned = _safe_str(item)
                if cleaned:
                    lines.append(f"- {cleaned}")
    note = "\n".join(lines).strip()
    if len(note) > max_chars:
        note = note[:max_chars].rstrip() + "...(truncated)"
    return note


def _read_log_tail(
    project_root: str,
    log_path: str,
    *,
    max_lines: int = 80,
    max_bytes: int = 200_000,
) -> str:
    if not log_path:
        return ""
    safe = _safe_path(project_root, log_path)
    if not safe or not os.path.isfile(safe):
        return ""
    try:
        if os.path.getsize(safe) > max_bytes:
            return ""
    except Exception:
        return ""
    try:
        with open(safe, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.read().splitlines()
    except Exception:
        return ""
    if not lines:
        return ""
    tail = lines[-max_lines:] if max_lines > 0 else lines
    return "\n".join(tail).strip()


def _format_applied_steps_summary(
    applied_steps: List[Dict[str, object]],
    project_root: str,
    *,
    max_items: int = 12,
) -> str:
    if not applied_steps:
        return "No files touched yet."
    lines = ["Files touched so far (most recent):"]
    for step in applied_steps[-max_items:]:
        if not isinstance(step, dict):
            continue
        raw_path = _safe_str(step.get("abs_path") or step.get("path"))
        if not raw_path:
            continue
        if os.path.isabs(raw_path):
            display = _display_path_for_report(project_root, raw_path)
        else:
            display = raw_path
        note = _safe_str(step.get("note"))
        marker = "code" if step.get("is_code") else "file"
        suffix = f" ({marker}{': ' + note if note else ''})"
        lines.append(f"- {display}{suffix}")
        if max_items and len(lines) - 1 >= max_items:
            break
    return "\n".join(lines)


def _format_progress_memory(progress_tracker: ProgressTracker, source_path: str, max_items: int = 6) -> str:
    if not progress_tracker:
        return ""
    summary = progress_tracker.summarize(source_path, max_items=max_items)
    if not summary:
        return ""
    return summary + "\n\n"


def _load_solver_config(config_path: str) -> Dict[str, object]:
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_search_engines(
    project_root: str,
    timeout: Optional[int],
    cache_ttl_hours: int,
) -> List[object]:
    engines: List[object] = []
    try:
        from credentials import get_search_credentials
    except Exception:
        return engines
    cache_root = os.path.join(project_root, ".research_cache", "project_solver_web")
    cfg_path = os.getenv("SOLVER_CONFIG_PATH", "config.json") or "config.json"
    config_locations = [
        os.path.abspath(cfg_path),
        os.path.abspath(os.path.join(project_root, cfg_path)),
    ]
    config = {}
    for path in config_locations:
        if os.path.isfile(path):
            config = _load_solver_config(path)
            if config:
                break
    search_configs = config.get("search_engines") if isinstance(config, dict) else []
    if isinstance(search_configs, list):
        for sc in search_configs:
            if not isinstance(sc, dict):
                continue
            if str(sc.get("type", "google")).lower() != "google":
                continue
            name = sc.get("name")
            key, cse = get_search_credentials(name)
            if key and cse:
                engines.append(
                    GoogleSearchEngine(
                        key,
                        cse,
                        timeout=timeout,
                        cache_ttl_hours=cache_ttl_hours,
                        cache_root=cache_root,
                    )
                )
    if not engines:
        key, cse = get_search_credentials(None)
        if key and cse:
            engines.append(
                GoogleSearchEngine(
                    key,
                    cse,
                    timeout=timeout,
                    cache_ttl_hours=cache_ttl_hours,
                    cache_root=cache_root,
                )
            )
    return engines


def _web_research_enabled(mode: str, engines: List[object]) -> bool:
    norm = (mode or "auto").strip().lower()
    if norm in {"0", "false", "no", "off", "never"}:
        return False
    if norm in {"1", "true", "yes", "on", "always"}:
        return True
    return bool(engines)


def _web_cache(project_root: str) -> WebResearchCache:
    root = os.path.join(project_root, ".research_cache")
    return WebResearchCache(root, "project_solver_web")


def _normalize_web_query(query: str, max_chars: int) -> str:
    return normalize_query(query, max_chars=max_chars, drop_todo_fixme=True)


def _extract_web_research_queries(
    source: RequirementSource,
    source_actions_log: List[str],
    *,
    limit: int = 2,
) -> List[str]:
    queries: List[str] = []
    max_chars = _env_int("SOLVER_WEB_RESEARCH_MAX_QUERY_CHARS", 512)
    recent_log = "\n".join(source_actions_log[-50:]) if source_actions_log else ""
    for match in MODULE_NOT_FOUND_RE.findall(recent_log):
        if match:
            queries.append(f"ModuleNotFoundError No module named '{match}' python fix")
    error_lines = []
    for line in recent_log.splitlines():
        if WEB_RESEARCH_TRIGGER_RE.search(line):
            error_lines.append(line.strip())
    if error_lines:
        queries.append(error_lines[-1])
    requirement_text = (source.requirements_text or "").lower()
    if any(token in requirement_text for token in ("documentation", "docs", "spec", "reference", "api")):
        snippet = " ".join(source.requirements_text.splitlines()[:2]).strip()
        if snippet:
            queries.append(snippet)
    deduped: List[str] = []
    seen = set()
    for query in queries:
        q = _normalize_web_query(query, max_chars)
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        deduped.append(q)
        if limit and len(deduped) >= limit:
            break
    return deduped


def _search_web(
    engines: List[object],
    query: str,
    *,
    max_results: int,
    project_root: str,
    cache_ttl_hours: int,
) -> List[Dict[str, str]]:
    max_chars = _env_int("SOLVER_WEB_RESEARCH_MAX_QUERY_CHARS", 512)
    cache = _web_cache(project_root)
    return search_web(
        engines,
        query,
        max_results=max_results,
        cache=cache,
        cache_ttl_hours=cache_ttl_hours,
        max_chars=max_chars,
        drop_todo_fixme=True,
    )


def _fetch_web_content(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    project_root: str,
    cache_ttl_hours: int,
) -> str:
    if not url:
        return ""
    cache = _web_cache(project_root)
    converter = FileConverter()
    return fetch_url_content(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        cache=cache,
        cache_ttl_hours=cache_ttl_hours,
        file_converter=converter,
        raise_on_error=False,
    )


def _summarize_web_research(
    provider,
    *,
    query: str,
    documents: List[Dict[str, str]],
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
) -> str:
    return summarize_web_research(
        provider,
        query=query,
        documents=documents,
        llm_max_tokens=llm_max_tokens,
        llm_temperature=llm_temperature,
        llm_timeout=llm_timeout,
        llm_reasoning_effort=llm_reasoning_effort,
    )


def _run_third_party_audit(
    *,
    provider,
    source: RequirementSource,
    requirements_register: Dict[str, object],
    requirements_register_index: str,
    source_actions_log: List[str],
    applied_steps: List[Dict[str, object]],
    project_root: str,
    log_excerpt: str,
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
    model_override: Optional[str],
    actions_log: List[str],
) -> Optional[Dict[str, object]]:
    def _model_override_allowed(target_provider, override: Optional[str]) -> bool:
        if not override:
            return False
        name = _safe_str(getattr(target_provider, "name", "")).lower()
        if name == "gemini":
            lowered = override.lower()
            return lowered.startswith("gemini") or lowered.startswith("models/gemini")
        return True

    system_prompt = (
        "You are an independent third-party auditor reviewing project progress. "
        "Evaluate alignment to requirements, detect scope drift or rabbit holes, and propose realignment actions. "
        "Return JSON only."
    )
    audit_requirements = _build_audit_requirements_section(requirements_register, source.path)
    files_touched = _format_applied_steps_summary(applied_steps, project_root)
    log_section = log_excerpt or "No log excerpt available."
    user_prompt = (
        f"{audit_requirements}\n\n"
        "Formal requirements index (reference IDs when relevant):\n"
        f"{requirements_register_index}\n\n"
        f"Requirement source: {source.path}\n"
        f"Source requirements:\n{source.requirements_text}\n\n"
        "Project progress context:\n"
        f"{files_touched}\n\n"
        "Recent actions log (most recent):\n"
        f"{_trim_actions_log(source_actions_log) if source_actions_log else 'None yet.'}\n\n"
        "Recent log excerpt:\n"
        f"{log_section}\n\n"
        "Notes:\n"
        "- The project is likely incomplete at this stage; partial work is expected.\n"
        "- Distinguish incomplete-but-on-track work from true scope drift or rabbit holes.\n"
        "- Highlight missing requirements only if they are clearly being ignored or displaced.\n\n"
        "Respond with JSON only. Schema:\n"
        "{\n"
        '  "status": "on_track|drift|blocked",\n'
        '  "summary": "short summary",\n'
        '  "missing_requirements": ["REQ-###", "..."],\n'
        '  "scope_creep": ["issue 1", "..."],\n'
        '  "risks": ["risk 1", "..."],\n'
        '  "recommendations": ["recommendation 1", "..."],\n'
        '  "realign_steps": ["action 1", "action 2"]\n'
        "}\n"
    )
    original_model = getattr(provider, "model", None)
    if model_override:
        if _model_override_allowed(provider, model_override):
            try:
                provider.model = model_override
            except Exception:
                pass
        else:
            actions_log.append(
                f"Skipped incompatible model override for audit provider: {model_override}."
            )
    try:
        resp = provider.predict(
            [{"role": "user", "content": user_prompt}],
            system=system_prompt,
            max_tokens=llm_max_tokens or 800,
            temperature=min(0.2, llm_temperature),
            timeout=llm_timeout,
            reasoning_effort=llm_reasoning_effort,
        )
    except Exception as exc:
        actions_log.append(f"Third-party audit failed for {source.path}: {exc}")
        return None
    finally:
        if model_override and original_model is not None:
            try:
                provider.model = original_model
            except Exception:
                pass
    try:
        payload = _parse_json_payload(resp.text or "{}")
    except json.JSONDecodeError as exc:
        actions_log.append(f"Third-party audit JSON parse failed for {source.path}: {exc}")
        return None
    if not isinstance(payload, dict):
        actions_log.append(f"Third-party audit returned non-dict payload for {source.path}.")
        return None
    return payload


def _format_requirement_label(requirement: object) -> Tuple[str, Optional[str], str]:
    if isinstance(requirement, dict):
        req_id = _safe_str(requirement.get("id"))
        title = _safe_str(requirement.get("title"))
        desc = _safe_str(requirement.get("description"))
        if req_id and title:
            return f"{req_id}: {title}", req_id, f"{title} {desc}".strip()
        if req_id and desc:
            return f"{req_id}: {desc}", req_id, desc
        if title:
            return title, req_id or None, f"{title} {desc}".strip()
        if desc:
            return desc, req_id or None, desc
        return req_id or "Unnamed requirement", req_id or None, desc
    label = _safe_str(requirement)
    return label or "Unnamed requirement", None, label


def _build_requirements_register_section(
    register: Dict[str, object],
    source_path: str,
    *,
    max_chars: int = 4000,
) -> str:
    requirements = register.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    index = _format_requirements_register_index(requirements, max_chars=max_chars)
    section = "Formal requirements register (use IDs like REQ-001 to reference requirements):\n"
    section += f"{index}\n"
    global_ids = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        sources = req.get("source")
        if isinstance(sources, str):
            sources = [sources]
        if isinstance(sources, list) and "global" in [s for s in sources if isinstance(s, str)]:
            req_id = _safe_str(req.get("id"))
            if req_id:
                global_ids.append(req_id)
    if global_ids:
        section += "\nGlobal requirement IDs (reference at least one in each plan step):\n"
        for req_id in global_ids:
            section += f"- {req_id}\n"
    sequence_ids = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        sources = req.get("source")
        if isinstance(sources, str):
            sources = [sources]
        if isinstance(sources, list) and "sequence_check" in [s for s in sources if isinstance(s, str)]:
            req_id = _safe_str(req.get("id"))
            if req_id:
                sequence_ids.append(req_id)
    if sequence_ids:
        section += "\nSequence requirement IDs (reference when sequence gaps exist):\n"
        for req_id in sequence_ids:
            section += f"- {req_id}\n"
    relevant = _requirements_for_source(requirements, source_path)
    if relevant:
        section += "\nRelevant IDs for this source:\n"
        for req in relevant:
            section += f"- {req.get('id')}: {_safe_str(req.get('title'))}\n"
    assumptions = register.get("assumptions")
    if isinstance(assumptions, list) and assumptions:
        section += "\nAssumptions:\n"
        for item in assumptions[:10]:
            section += f"- {_safe_str(item)}\n"
    open_questions = register.get("open_questions")
    if isinstance(open_questions, list) and open_questions:
        section += "\nOpen questions:\n"
        for item in open_questions[:10]:
            section += f"- {_safe_str(item)}\n"
    return section.strip() + "\n\n"


def _format_todo_markdown(todo_summary: Dict[str, object], max_items: int = 200) -> str:
    items = todo_summary.get("items") if isinstance(todo_summary, dict) else None
    if not isinstance(items, list):
        items = []
    lines = [
        "| # | Type | Source | Status | Notes | Command |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for idx, item in enumerate(items, start=1):
        if max_items and idx > max_items:
            lines.append("| ... | ... | ... | ... | ... | ... |")
            break
        if not isinstance(item, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                _escape_md_cell(str(value))
                for value in (
                    idx,
                    _safe_str(item.get("type")),
                    _safe_str(item.get("source")),
                    _safe_str(item.get("status")),
                    _safe_str(item.get("notes")),
                    _safe_str(item.get("command")),
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _collect_requirement_refs_from_plans(plans: List[Dict[str, object]]) -> set:
    refs: set = set()
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        plan_steps = plan.get("plan")
        if not isinstance(plan_steps, list):
            continue
        refs.update(_extract_requirement_refs_from_plan(plan_steps, plan.get("requirements")))
    return refs


def _dedupe_todo_items(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    seen = set()
    deduped: List[Dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            _safe_str(item.get("type")),
            _safe_str(item.get("source")),
            _safe_str(item.get("status")),
            _safe_str(item.get("notes")),
            _safe_str(item.get("command")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _is_verification_failure(failure: Dict[str, object]) -> bool:
    command = failure.get("command")
    if isinstance(command, str) and _is_verification_command(command):
        return True
    return bool(failure.get("verification_issue"))


def _build_indentation_recovery_steps(
    failures: List[Dict[str, object]],
    *,
    project_root: str,
    actions_log: List[str],
) -> Optional[List[Dict[str, object]]]:
    affected_files: List[str] = []
    for failure in failures:
        stdout = failure.get("stdout") or ""
        stderr = failure.get("stderr") or ""
        output = f"{stdout}\n{stderr}"
        if not INDENTATION_ERROR_RE.search(output):
            continue
        affected_files.extend(_extract_error_file_paths(output))
    if not affected_files:
        return None
    unique_files = []
    seen = set()
    for path in affected_files:
        if path in seen:
            continue
        seen.add(path)
        unique_files.append(path)
    normalized = 0
    for path in unique_files:
        abs_path = _safe_path(project_root, path)
        if not abs_path:
            abs_path = _safe_path(project_root, os.path.join(project_root, path))
        if not abs_path:
            continue
        if _normalize_python_file(abs_path):
            normalized += 1
            actions_log.append(f"Normalized indentation in: {abs_path}")
    if normalized:
        actions_log.append(f"Indentation auto-fix applied to {normalized} file(s).")
    steps: List[Dict[str, object]] = []
    for failure in failures:
        command = failure.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        workdir = failure.get("workdir") or "."
        steps.append(
            {
                "type": "run_command",
                "step": "Retry failed command after indentation normalization",
                "command": command,
                "workdir": workdir,
                "timeout": 900,
            }
        )
    return steps if steps else None

def _plan_references_requirement_ids(
    plan_steps: List[Dict[str, object]],
    payload_requirements: object,
    requirement_ids: set,
) -> bool:
    refs = _extract_requirement_refs_from_plan(plan_steps, payload_requirements)
    return bool(refs & requirement_ids)


def _extract_requirement_refs_from_plan(
    plan_steps: List[Dict[str, object]],
    payload_requirements: object,
) -> set:
    refs: set = set()
    for item in _coerce_list(payload_requirements):
        refs.update({m.group(0).upper() for m in REQ_ID_RE.finditer(item)})
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        for key in ("step", "note", "content", "command", "path", "find", "replace"):
            val = step.get(key)
            if not isinstance(val, str):
                continue
            snippet = val if len(val) <= 2000 else val[:2000]
            refs.update({m.group(0).upper() for m in REQ_ID_RE.finditer(snippet)})
    return refs


def _extract_requirement_refs_from_step(step: Dict[str, object]) -> List[str]:
    if not isinstance(step, dict):
        return []
    refs: set = set()
    for key in ("step", "note", "content", "command", "path", "find", "replace"):
        val = step.get(key)
        if not isinstance(val, str):
            continue
        snippet = val if len(val) <= 2000 else val[:2000]
        refs.update({m.group(0).upper() for m in REQ_ID_RE.finditer(snippet)})
    req_list = step.get("requirements")
    if isinstance(req_list, list):
        for item in req_list:
            if isinstance(item, str):
                refs.update({m.group(0).upper() for m in REQ_ID_RE.finditer(item)})
    return sorted(refs)


def _plan_steps_missing_requirement_refs(plan_steps: List[Dict[str, object]]) -> List[int]:
    missing: List[int] = []
    for idx, step in enumerate(plan_steps):
        if not isinstance(step, dict):
            missing.append(idx)
            continue
        found = False
        for key in ("step", "note", "content"):
            val = step.get(key)
            if isinstance(val, str) and REQ_ID_RE.search(val):
                found = True
                break
        if not found:
            missing.append(idx)
    return missing


def _ensure_plan_requirement_refs(
    plan_steps: List[Dict[str, object]],
    *,
    payload_requirements: object,
    required_ids: set,
    global_ids: set,
    sequence_ids: set,
    all_ids: set,
    strict: bool,
) -> bool:
    if not isinstance(plan_steps, list) or not plan_steps:
        return False
    refs = _extract_requirement_refs_from_plan(plan_steps, payload_requirements)
    fallback_candidates: List[str] = []
    for group in (required_ids, global_ids, sequence_ids, all_ids, refs):
        for item in group:
            if isinstance(item, str) and REQ_ID_RE.match(item.upper()):
                fallback_candidates.append(item.upper())
        if fallback_candidates:
            break
    fallback = fallback_candidates[0] if fallback_candidates else None
    if not fallback:
        return False

    changed = False
    missing_steps = _plan_steps_missing_requirement_refs(plan_steps)
    for idx in missing_steps:
        step = plan_steps[idx]
        if not isinstance(step, dict):
            continue
        target_key = None
        for key in ("step", "note", "content"):
            val = step.get(key)
            if isinstance(val, str) and val.strip():
                target_key = key
                break
        if target_key is None:
            step["step"] = f"Auto-annotated step ({fallback})"
            changed = True
            continue
        if fallback not in step[target_key]:
            step[target_key] = f"{step[target_key].rstrip()} ({fallback})"
            changed = True

    if strict:
        # Ensure required/global/sequence IDs are referenced at least once.
        refs = _extract_requirement_refs_from_plan(plan_steps, payload_requirements)
        missing_required = [rid for rid in sorted(required_ids) if rid not in refs]
        missing_global = [rid for rid in sorted(global_ids) if rid not in refs]
        missing_sequence = [rid for rid in sorted(sequence_ids) if rid not in refs]
        if missing_required or missing_global or missing_sequence:
            extra_ids = missing_required + missing_global + missing_sequence
            extra_suffix = " (" + ", ".join(extra_ids) + ")"
            for step in plan_steps:
                if not isinstance(step, dict):
                    continue
                if isinstance(step.get("step"), str):
                    step["step"] = step["step"].rstrip() + extra_suffix
                    changed = True
                    break
                if isinstance(step.get("note"), str):
                    step["note"] = step["note"].rstrip() + extra_suffix
                    changed = True
                    break
    return changed


def _ensure_audit_step(plan_steps: List[Dict[str, object]], audit_note: str) -> bool:
    if not plan_steps or not audit_note:
        return False
    if _plan_has_audit_marker(plan_steps):
        return False
    note = audit_note.strip()
    if len(note) > 300:
        note = note[:300].rstrip() + "..."
    plan_steps.insert(0, {"type": "note", "step": f"AUDIT: {note}"})
    return True


def _normalize_plan_step_paths(plan_steps: List[Dict[str, object]]) -> bool:
    if not plan_steps:
        return False
    changed = False
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        for key in ("path", "step", "note", "content", "command", "find", "replace"):
            val = step.get(key)
            if not isinstance(val, str) or not val:
                continue
            updated = re.sub(r"\bsolutions(\d+)\.py\b", r"solution\1.py", val, flags=re.IGNORECASE)
            updated = re.sub(r"\btest_solutions(\d+)\.py\b", r"test_solution\1.py", updated, flags=re.IGNORECASE)
            if updated != val:
                step[key] = updated
                changed = True
    return changed


def _plan_has_actionable_steps(plan_steps: List[Dict[str, object]]) -> bool:
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        step_type = _normalize_step_type(step.get("type"))
        if step_type in {"write_file", "append_file", "replace_in_file", "run_command", "create_dir"}:
            return True
    return False


def _extract_plan_text(plan_steps: List[Dict[str, object]]) -> str:
    chunks: List[str] = []
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        for key in ("step", "content", "find", "replace", "command", "path"):
            val = step.get(key)
            if isinstance(val, str) and val:
                chunks.append(val)
    return "\n".join(chunks)


def _plan_suspicious_issues(
    plan_steps: List[Dict[str, object]],
    eval_info: Dict[str, object],
) -> List[str]:
    issues: List[str] = []
    if not plan_steps:
        return issues
    eval_keys = eval_info.get("keys") if isinstance(eval_info, dict) else None
    eval_keys = eval_keys if isinstance(eval_keys, list) else []
    label_values = []
    if isinstance(eval_info, dict):
        label_values = eval_info.get("label_values") or []
    label_variants = set()
    for val in label_values:
        if not isinstance(val, str):
            continue
        label_variants.add(val.lower())
        label_variants.add(val.replace("_", " ").lower())
    text = _extract_plan_text(plan_steps)
    if eval_keys:
        allowed_attrs = {"get", "items", "keys", "values"}
        for match in re.finditer(r"\bdata\.([A-Za-z_]\w*)", text):
            attr = match.group(1)
            if attr in allowed_attrs:
                continue
            if attr not in eval_keys:
                issues.append(
                    f"Uses attribute access data.{attr} but eval_data.json keys are {', '.join(eval_keys)}"
                )
                break
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        path = step.get("path")
        content = step.get("content")
        if not isinstance(content, str):
            continue
        if isinstance(path, str):
            base = os.path.basename(path)
            if re.match(r"^solutions\d+\.py$", base, re.IGNORECASE):
                issues.append("Writes to pluralised solutionsN.py; use solutionN.py")
                break
            if re.match(r"^test_solutions\d+\.py$", base, re.IGNORECASE):
                issues.append("Writes to pluralised test_solutionsN.py; use test_solutionN.py")
                break
        if isinstance(path, str) and "solution" in path.lower():
            if label_variants and ("generate_text" in content or "generate_with_pdf" in content):
                if not any(label in content.lower() for label in label_variants):
                    issues.append("LLM output not mapped to canonical labels from eval_data")
                    break
            if "PDFDocument(" in content and "PDFDocument.load" not in content:
                issues.append("Constructs PDFDocument directly; use PDFDocument.load(file_path)")
                break
            if "latlong" in content.lower():
                if "['lat']" in content or "['lon']" in content or ".get('lat')" in content or ".get('lon')" in content:
                    issues.append("Treats latlong as dict; eval_data latlong is list [lat, lon]")
                    break
            if "search_address" in content and ("['lat']" in content or "['lon']" in content or ".get('lat')" in content or ".get('lon')" in content):
                issues.append("Treats search_address result as dict; helper returns tuple[lat, lon]")
                break
            if re.search(r"get_page\([^)]*\)\.base64encode", content):
                issues.append("Uses get_page(...).base64encode(); get_page returns an Image, not a PDFDocument")
                break
            if re.search(r"generate_with_image\(\s*\w*client", content):
                issues.append("Calls generate_with_image() with a client argument; helper API expects (text_prompt, image_bytes[, model])")
                break
    return issues


def _plan_python_syntax_issues(plan_steps: List[Dict[str, object]]) -> List[str]:
    issues: List[str] = []
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        if _normalize_step_type(step.get("type")) != "write_file":
            continue
        path = step.get("path")
        content = step.get("content")
        if not isinstance(path, str) or not path.endswith(".py"):
            continue
        if not isinstance(content, str):
            continue
        try:
            ast.parse(content)
        except SyntaxError as exc:
            detail = exc.msg
            if exc.lineno:
                detail = f"{detail} (line {exc.lineno})"
            issues.append(f"Invalid Python syntax in {path}: {detail}")
    return issues


def _build_requirements_register(
    requirement_sources: List[RequirementSource],
    context_summary: str,
    provider,
    *,
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
    actions_log: List[str],
) -> Tuple[Dict[str, object], str]:
    corpus = _build_requirements_corpus(requirement_sources, context_summary)
    system_prompt = (
        "You are a principal requirements engineer. "
        "Create a formal requirements register with atomic, testable, unambiguous requirements."
    )
    user_prompt = (
        "Source requirements and context:\n"
        f"{corpus}\n\n"
        "Task:\n"
        "- Produce a formal requirements register using best-practice requirements engineering.\n"
        "- Assign IDs in the format REQ-001, REQ-002, sequential and unique.\n"
        "- Each requirement must be clear, testable, and implementation-ready.\n"
        "- Include acceptance criteria and verification guidance.\n"
        "- Distinguish functional requirements, non-functional requirements, constraints, and assumptions.\n"
        "- If requirements are missing or ambiguous, add items to open_questions instead of inventing details.\n"
        "- If you infer a requirement, note it in the notes field as inferred.\n\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "requirements": [\n'
        "    {\n"
        '      "id": "REQ-001",\n'
        '      "title": "short title",\n'
        '      "description": "full requirement description",\n'
        '      "type": "functional|non-functional|constraint|assumption",\n'
        '      "priority": "must|should|could|wont",\n'
        '      "source": ["path1", "path2"],\n'
        '      "acceptance_criteria": ["criterion 1", "criterion 2"],\n'
        '      "dependencies": ["REQ-002"],\n'
        '      "verification": "how to verify",\n'
        '      "rationale": "why this matters",\n'
        '      "notes": "inferred/risks/etc."\n'
        "    }\n"
        "  ],\n"
        '  "assumptions": ["assumption 1"],\n'
        '  "open_questions": ["open question 1"]\n'
        "}\n"
    )
    try:
        resp = provider.predict(
            [{"role": "user", "content": user_prompt}],
            system=system_prompt,
            max_tokens=llm_max_tokens or 10240,
            temperature=min(0.2, llm_temperature),
            timeout=llm_timeout,
            reasoning_effort=llm_reasoning_effort,
        )
        payload = _parse_json_payload(resp.text or "{}")
        register = _normalize_requirements_register(payload, requirement_sources)
        register = _ensure_global_requirements(register, requirement_sources=requirement_sources)
        if register.get("requirements"):
            return register, "llm"
        actions_log.append("LLM requirements register returned no requirements; using fallback register.")
    except Exception as exc:
        actions_log.append(f"Failed to generate requirements register via LLM: {exc}")
    fallback_register = _fallback_requirements_register(requirement_sources, context_summary)
    fallback_register = _ensure_global_requirements(
        fallback_register, requirement_sources=requirement_sources
    )
    return fallback_register, "fallback"


def _build_requirement_coverage(
    requirement_sources: List[RequirementSource],
    applied_steps_by_source: Dict[str, List[Dict[str, object]]],
    project_root: str,
    source_logs: Optional[Dict[str, List[str]]] = None,
    requirements_register: Optional[Dict[str, object]] = None,
) -> Tuple[Dict[str, object], List[str]]:
    coverage: Dict[str, object] = {}
    missing_sources: List[str] = []
    register_requirements = []
    if isinstance(requirements_register, dict):
        register_requirements = requirements_register.get("requirements") or []
    if not isinstance(register_requirements, list):
        register_requirements = []
    for source in requirement_sources:
        requirements: List[object] = []
        if register_requirements:
            register_matches = _requirements_for_source(
                register_requirements, source.path, max_items=0
            )
            if register_matches:
                requirements = list(register_matches)
        if not requirements:
            requirements = _extract_source_requirements(source)
        applied_steps = list(applied_steps_by_source.get(source.path, []))
        if source_logs and source.path in source_logs:
            for entry in source_logs.get(source.path, []):
                ref = _file_ref_from_log_entry(entry)
                if ref:
                    applied_steps.append(ref)
        file_refs: List[Dict[str, object]] = []
        for step in applied_steps:
            raw_path = step.get("abs_path") or step.get("path")
            if not isinstance(raw_path, str):
                continue
            abs_path = raw_path
            if not os.path.isabs(abs_path):
                abs_path = os.path.abspath(os.path.join(project_root, abs_path))
            display_path = _display_path_for_report(project_root, abs_path)
            line = step.get("line") if isinstance(step.get("line"), int) else 1
            ref = {
                "path": display_path,
                "line": line,
                "note": step.get("note"),
                "is_code": bool(step.get("is_code")),
            }
            file_refs.append(ref)
        deduped_refs = []
        seen_refs = set()
        for ref in file_refs:
            key = (ref.get("path"), ref.get("line"), ref.get("note"))
            if key in seen_refs:
                continue
            seen_refs.add(key)
            deduped_refs.append(ref)
        file_refs = deduped_refs
        code_refs = [ref for ref in file_refs if ref.get("is_code")]
        requirements_report = []
        missing_requirements = []
        for requirement in requirements:
            label, req_id, req_text = _format_requirement_label(requirement)
            requires_code = bool(CODE_REQUIREMENT_RE.search(req_text or label))
            refs = code_refs if (requires_code and code_refs) else file_refs
            if requires_code and not code_refs:
                requirements_report.append(
                    {
                        "requirement": label,
                        "requirement_id": req_id,
                        "status": "missing",
                        "files": [],
                    }
                )
                missing_requirements.append(label)
                continue
            if refs:
                requirements_report.append(
                    {
                        "requirement": label,
                        "requirement_id": req_id,
                        "status": "covered",
                        "files": refs,
                    }
                )
            else:
                requirements_report.append(
                    {
                        "requirement": label,
                        "requirement_id": req_id,
                        "status": "missing",
                        "files": [],
                    }
                )
                missing_requirements.append(label)
        coverage[source.path] = {
            "requirements": requirements_report,
            "files_touched": file_refs,
            "missing_requirements": missing_requirements,
        }
        if missing_requirements:
            missing_sources.append(source.path)
    return coverage, missing_sources


def _build_requirement_traceability(
    requirements_register: Optional[Dict[str, object]],
    plans: List[Dict[str, object]],
    applied_steps_by_source: Dict[str, List[Dict[str, object]]],
    project_root: str,
) -> Dict[str, object]:
    register_reqs = []
    if isinstance(requirements_register, dict):
        register_reqs = requirements_register.get("requirements") or []
    if not isinstance(register_reqs, list):
        register_reqs = []

    req_meta: Dict[str, Dict[str, object]] = {}
    req_ids: set = set()
    for req in register_reqs:
        if not isinstance(req, dict):
            continue
        req_id = _safe_str(req.get("id")).upper()
        if not req_id:
            continue
        req_ids.add(req_id)
        req_meta[req_id] = {
            "title": _safe_str(req.get("title")),
            "description": _safe_str(req.get("description")),
            "type": _safe_str(req.get("type")),
            "priority": _safe_str(req.get("priority")),
            "source": req.get("source"),
        }

    plan_refs_by_req: Dict[str, List[Dict[str, object]]] = {}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        plan_steps = plan.get("plan")
        if not isinstance(plan_steps, list):
            continue
        source_path = _safe_str(plan.get("source_path"))
        iteration = plan.get("iteration")
        for idx, step in enumerate(plan_steps):
            if not isinstance(step, dict):
                continue
            refs = _extract_requirement_refs_from_step(step)
            if not refs:
                continue
            summary = _safe_str(step.get("step") or step.get("note") or step.get("content"))
            if summary and len(summary) > 200:
                summary = summary[:200].rstrip() + "..."
            entry = {
                "source": source_path,
                "iteration": iteration if isinstance(iteration, int) else None,
                "step_index": idx,
                "step_type": _normalize_step_type(step.get("type")),
                "summary": summary,
            }
            for req_id in refs:
                req_ids.add(req_id)
                plan_refs_by_req.setdefault(req_id, []).append(entry)

    change_refs_by_req: Dict[str, List[Dict[str, object]]] = {}
    for source_path, steps in applied_steps_by_source.items():
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            refs = step.get("requirement_ids")
            if not isinstance(refs, list) or not refs:
                refs = _extract_requirement_refs_from_step(step)
            refs = [r for r in refs if isinstance(r, str)]
            if not refs:
                continue
            raw_path = step.get("abs_path") or step.get("path")
            if not isinstance(raw_path, str):
                continue
            abs_path = raw_path
            if not os.path.isabs(abs_path):
                abs_path = os.path.abspath(os.path.join(project_root, abs_path))
            display_path = _display_path_for_report(project_root, abs_path)
            ref_entry = {
                "source": source_path,
                "path": display_path,
                "line": step.get("line") if isinstance(step.get("line"), int) else 1,
                "type": _safe_str(step.get("type")),
                "note": _safe_str(step.get("note")),
                "is_code": bool(step.get("is_code")),
            }
            for req_id in refs:
                req_id = req_id.upper()
                req_ids.add(req_id)
                change_refs_by_req.setdefault(req_id, []).append(ref_entry)

    for req_id, refs in change_refs_by_req.items():
        seen = set()
        deduped: List[Dict[str, object]] = []
        for ref in refs:
            key = (
                _safe_str(ref.get("source")),
                _safe_str(ref.get("path")),
                ref.get("line"),
                _safe_str(ref.get("type")),
                _safe_str(ref.get("note")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ref)
        change_refs_by_req[req_id] = deduped

    unknown_ids = sorted([rid for rid in req_ids if rid not in req_meta])
    requirements_trace: List[Dict[str, object]] = []
    for req_id in sorted(req_ids):
        plan_refs = plan_refs_by_req.get(req_id, [])
        change_refs = change_refs_by_req.get(req_id, [])
        status = "covered" if change_refs else ("planned" if plan_refs else "unmapped")
        meta = req_meta.get(req_id, {})
        requirements_trace.append(
            {
                "id": req_id,
                "title": meta.get("title"),
                "description": meta.get("description"),
                "type": meta.get("type"),
                "priority": meta.get("priority"),
                "source": meta.get("source"),
                "status": status,
                "plan_steps": plan_refs,
                "changes": change_refs,
            }
        )

    summary = {
        "total": len(requirements_trace),
        "with_changes": sum(1 for item in requirements_trace if item.get("status") == "covered"),
        "planned_only": sum(1 for item in requirements_trace if item.get("status") == "planned"),
        "unmapped": sum(1 for item in requirements_trace if item.get("status") == "unmapped"),
        "unknown_requirement_ids": unknown_ids,
    }
    return {
        "summary": summary,
        "requirements": requirements_trace,
    }


def _opencode_threshold() -> int:
    try:
        return max(1, int(os.getenv("OPENCODE_FALLBACK_THRESHOLD", "1")))
    except Exception:
        return 1


def _codex_cli_mode() -> str:
    raw = os.getenv("CODEX_USE_CLI") or os.getenv("CODEX_CLI_MODE") or "auto"
    norm = raw.strip().lower()
    if norm in {"1", "true", "yes", "on", "force", "required"}:
        return "force"
    if norm in {"0", "false", "no", "off", "never"}:
        return "never"
    return "auto"


def _codingagent_primary_mode() -> str:
    raw = os.getenv("CODINGAGENT_MODE", "fallback")
    norm = raw.strip().lower()
    if norm in {"primary", "force", "always"}:
        return "primary"
    return "fallback"


def _codex_config_path() -> str:
    codex_home = os.getenv("CODEX_HOME") or os.path.expanduser("~/.codex")
    return os.path.join(codex_home, "config.toml")


def _read_codex_config_value(key: str) -> Optional[str]:
    config_path = _codex_config_path()
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if not stripped.lower().startswith(key.lower()):
                    continue
                parts = stripped.split("=", 1)
                if len(parts) != 2:
                    continue
                value = parts[1].strip().strip('"').strip("'")
                return value or None
    except Exception:
        return None
    return None


def _codex_auth_cache_path() -> str:
    codex_home = os.getenv("CODEX_HOME") or os.path.expanduser("~/.codex")
    return os.path.join(codex_home, "auth.json")


def _codex_preflight(
    *,
    allow_run: bool,
    actions_log: List[str],
    codingagent_primary: Optional[str],
    codingagent_fallback: Optional[str],
) -> Dict[str, object]:
    global CODEX_PREFLIGHT_STATE
    codex_requested = "codex" in {
        codingagent_primary or "",
        codingagent_fallback or "",
    }
    cli_mode = _codex_cli_mode()
    state: Dict[str, object] = {
        "requested": codex_requested,
        "cli_mode": cli_mode,
        "cli_ready": None,
        "auth_ready": None,
        "auth_source": None,
        "auth_message": None,
    }
    if not codex_requested:
        CODEX_PREFLIGHT_STATE = state
        return state

    if cli_mode == "never":
        state["cli_ready"] = False
        state["auth_ready"] = None
        state["auth_message"] = "Codex CLI disabled (CODEX_USE_CLI=never); provider fallback only."
        actions_log.append(state["auth_message"])
        CODEX_PREFLIGHT_STATE = state
        return state

    if not allow_run:
        state["cli_ready"] = False
        state["auth_ready"] = None
        state["auth_message"] = (
            "Codex CLI disabled because run commands are not allowed; provider fallback only."
        )
        actions_log.append(state["auth_message"])
        CODEX_PREFLIGHT_STATE = state
        return state

    codex_bin = os.getenv("CODEX_BIN", "codex")
    resolved = shutil.which(codex_bin)
    if not resolved:
        auto_install = os.getenv("CODEX_AUTO_INSTALL", "0").strip().lower() not in ("0", "false", "no")
        if allow_run and auto_install:
            state["cli_ready"] = None
            message = (
                "Codex CLI not found; will attempt auto-install on first use. "
                "You can also install via `npm i -g @openai/codex`."
            )
        else:
            state["cli_ready"] = False
            message = (
                "Codex CLI not found. Install via `npm i -g @openai/codex` "
                "or set CODEX_BIN to the codex executable."
            )
        actions_log.append(message)
        state["auth_message"] = message
        CODEX_PREFLIGHT_STATE = state
        return state
    state["cli_ready"] = True

    if os.getenv("CODEX_API_KEY"):
        state["auth_ready"] = True
        state["auth_source"] = "CODEX_API_KEY"
        state["auth_message"] = "Codex preflight: using CODEX_API_KEY for non-interactive auth."
    elif os.getenv("OPENAI_API_KEY"):
        state["auth_ready"] = True
        state["auth_source"] = "OPENAI_API_KEY"
        state["auth_message"] = "Codex preflight: using OPENAI_API_KEY for non-interactive auth."
    else:
        auth_path = _codex_auth_cache_path()
        if os.path.exists(auth_path):
            state["auth_ready"] = True
            state["auth_source"] = auth_path
            state["auth_message"] = f"Codex preflight: found cached auth at {auth_path}."
        else:
            store = _read_codex_config_value("cli_auth_credentials_store") or "auto"
            if store in {"keyring", "auto"}:
                state["auth_ready"] = None
                state["auth_source"] = store
                state["auth_message"] = (
                    "Codex preflight: credentials may be stored in the OS keyring; "
                    "unable to verify locally."
                )
            else:
                state["auth_ready"] = False
                state["auth_source"] = store
                state["auth_message"] = (
                    "Codex preflight: no credentials detected. "
                    "Run `codex login --device-auth` or set OPENAI_API_KEY/CODEX_API_KEY."
                )

    if state.get("auth_message"):
        actions_log.append(state["auth_message"])
    CODEX_PREFLIGHT_STATE = state
    return state


def _ensure_codex_available(allow_run: bool, actions_log: List[str]) -> Optional[str]:
    bin_path = os.getenv("CODEX_BIN", "codex")
    resolved = shutil.which(bin_path)
    if resolved:
        return resolved
    if not allow_run:
        actions_log.append("Codex CLI unavailable (codex not found; run commands disabled).")
        return None
    auto_install = os.getenv("CODEX_AUTO_INSTALL", "0")
    if auto_install.strip().lower() in ("0", "false", "no"):
        actions_log.append(
            "Codex CLI unavailable (codex not installed; auto-install disabled). "
            "Install via `npm i -g @openai/codex` (recommended) or update CODEX_BIN."
        )
        return None
    install_cmd = os.getenv("CODEX_INSTALL_COMMAND") or "npm i -g @openai/codex"
    actions_log.append(f"Attempting to install Codex CLI via `{install_cmd}`.")
    try:
        subprocess.run(install_cmd, shell=True, check=False, text=True)
    except Exception as exc:
        actions_log.append(f"Codex CLI install failed: {exc}")
        return None
    resolved = shutil.which(bin_path)
    if not resolved:
        actions_log.append(
            "Codex CLI install completed but codex binary still not found. "
            "Verify install paths or try another method."
        )
        return None
    return resolved


def _ensure_opencode_available(allow_run: bool, actions_log: List[str]) -> Optional[str]:
    bin_path = os.getenv("OPENCODE_BIN", "opencode")
    resolved = shutil.which(bin_path)
    if resolved:
        return resolved
    if not allow_run:
        actions_log.append("OpenCode fallback unavailable (opencode not found; run commands disabled).")
        return None
    auto_install = os.getenv("OPENCODE_AUTO_INSTALL", "1")
    if auto_install.strip().lower() in ("0", "false", "no"):
        actions_log.append(
            "OpenCode fallback unavailable (opencode not installed; auto-install disabled). "
            "Install via `curl -fsSL https://opencode.ai/install | bash` or `npm install -g opencode-ai` "
            "(see opencode.txt)."
        )
        return None
    install_cmd = os.getenv("OPENCODE_INSTALL_COMMAND") or "curl -fsSL https://opencode.ai/install | bash"
    actions_log.append(f"Attempting OpenCode install: {install_cmd}")
    try:
        result = subprocess.run(
            install_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=900,
        )
        actions_log.append(
            f"OpenCode install exit code: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    except Exception as exc:
        actions_log.append(f"OpenCode install failed: {exc}")
        return None
    resolved = shutil.which(bin_path)
    if resolved:
        return resolved
    actions_log.append(
        "OpenCode install completed but opencode binary still not found. "
        "Verify install paths or try another method (see opencode.txt)."
    )
    return None


def _build_opencode_command(
    template: str,
    *,
    opencode_bin: str,
    opencode_model_flag: str,
    prompt: str,
    prompt_file: str,
    workspace: str,
    output_path: str,
) -> str:
    safe_prompt = shlex.quote(prompt)
    return template.format(
        opencode_bin=shlex.quote(opencode_bin),
        opencode_model_flag=opencode_model_flag,
        prompt=safe_prompt,
        prompt_file=shlex.quote(prompt_file),
        workspace=shlex.quote(workspace),
        output_path=shlex.quote(output_path),
    )


def _query_opencode_plan(
    *,
    prompt: str,
    workspace: str,
    output_path: str,
    allow_run: bool,
    actions_log: List[str],
    llm_provider: Optional[str],
    llm_api_key: Optional[str],
) -> Optional[Dict[str, object]]:
    if not allow_run:
        actions_log.append("OpenCode fallback skipped (run commands disabled).")
        return None
    bin_path = _ensure_opencode_available(allow_run, actions_log)
    if not bin_path:
        return None
    template = os.getenv("OPENCODE_COMMAND_TEMPLATE")
    if not template:
        template = DEFAULT_OPENCODE_COMMAND_TEMPLATE
        actions_log.append("OpenCode command template not set; using default opencode run template.")

    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", prefix="opencode_prompt_", suffix=".txt") as handle:
            handle.write(prompt)
            prompt_file = handle.name
    except Exception as exc:
        actions_log.append(f"Failed to write OpenCode prompt file: {exc}")
        return None

    opencode_model = os.getenv("OPENCODE_MODEL")
    opencode_model_flag = f"--model {shlex.quote(opencode_model)}" if opencode_model else ""
    cmd = _build_opencode_command(
        template,
        opencode_bin=bin_path,
        opencode_model_flag=opencode_model_flag,
        prompt=prompt,
        prompt_file=prompt_file,
        workspace=workspace,
        output_path=output_path,
    )
    actions_log.append(f"Querying OpenCode via command: {cmd}")
    env = os.environ.copy()
    if llm_api_key and llm_provider:
        if llm_provider in ("openai", "gpt", "chatgpt"):
            env.setdefault("OPENAI_API_KEY", llm_api_key)
        elif llm_provider in ("gemini", "google"):
            env.setdefault("GEMINI_API_KEY", llm_api_key)
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("OPENCODE_TIMEOUT", "900")),
            cwd=workspace,
            env=env,
        )
    except Exception as exc:
        actions_log.append(f"OpenCode command failed: {exc}")
        return None
    finally:
        try:
            os.unlink(prompt_file)
        except OSError:
            pass

    actions_log.append(
        f"OpenCode exit code: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    if result.returncode != 0:
        return None
    payload = _parse_opencode_output(result.stdout)
    if not payload:
        actions_log.append(
            "OpenCode response did not contain valid JSON payload. "
            "Ensure OpenCode is configured (e.g., `opencode auth login`, `opencode auth list`)."
        )
        return None
    return payload if isinstance(payload, dict) else None


def _build_codex_cli_command(
    *,
    codex_bin: str,
    workspace: str,
    output_file: str,
    sandbox_mode: Optional[str],
    skip_git_check: bool,
    model: Optional[str],
    ephemeral: bool,
    reasoning_effort: Optional[str],
) -> List[str]:
    cmd = [codex_bin, "exec"]
    if workspace:
        cmd += ["--cd", workspace]
    if sandbox_mode:
        cmd += ["--sandbox", sandbox_mode]
    if ephemeral:
        cmd.append("--ephemeral")
    if skip_git_check:
        cmd.append("--skip-git-repo-check")
    if model:
        cmd += ["--model", model]
    if reasoning_effort:
        cmd += ["--config", f"reasoning.effort={reasoning_effort}"]
    cmd += ["--output-last-message", output_file, "--color", "never", "-"]
    return cmd


def _query_codex_cli_plan(
    *,
    prompt: str,
    system_prompt: str,
    workspace: str,
    allow_run: bool,
    actions_log: List[str],
    llm_provider: Optional[str],
    llm_api_key: Optional[str],
    model_override: Optional[str],
    reasoning_effort: Optional[str],
) -> Optional[Dict[str, object]]:
    if not allow_run:
        actions_log.append("Codex CLI skipped (run commands disabled).")
        return None
    bin_path = _ensure_codex_available(allow_run, actions_log)
    if not bin_path:
        return None
    sandbox_mode = os.getenv("CODEX_SANDBOX", "read-only").strip() or None
    skip_git_check = os.getenv("CODEX_SKIP_GIT_CHECK", "1").strip().lower() not in ("0", "false", "no")
    model = model_override or os.getenv("CODEX_MODEL") or None
    ephemeral = os.getenv("CODEX_EPHEMERAL", "1").strip().lower() not in ("0", "false", "no")
    timeout = int(os.getenv("CODEX_TIMEOUT", "900"))
    output_file = None
    full_prompt = prompt
    if system_prompt:
        full_prompt = (
            "Instructions (higher priority than task text):\n"
            f"{system_prompt.strip()}\n\n"
            "Task:\n"
            f"{prompt}"
        )
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", prefix="codex_output_", suffix=".txt") as handle:
            output_file = handle.name
        cmd = _build_codex_cli_command(
            codex_bin=bin_path,
            workspace=workspace,
            output_file=output_file,
            sandbox_mode=sandbox_mode,
            skip_git_check=skip_git_check,
            model=model,
            ephemeral=ephemeral,
            reasoning_effort=_normalize_reasoning_effort(reasoning_effort),
        )
        env = os.environ.copy()
        if "CODEX_API_KEY" not in env:
            if env.get("OPENAI_API_KEY"):
                env["CODEX_API_KEY"] = env["OPENAI_API_KEY"]
            elif (llm_provider or "").lower() in {"openai", "gpt", "chatgpt"} and llm_api_key:
                env["CODEX_API_KEY"] = llm_api_key
        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except Exception as exc:
        actions_log.append(f"Codex CLI command failed: {exc}")
        if output_file:
            try:
                os.unlink(output_file)
            except OSError:
                pass
        return None

    actions_log.append(
        f"Codex CLI exit code: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    response_text = ""
    if output_file and os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as handle:
                response_text = handle.read()
        except Exception as exc:
            actions_log.append(f"Codex CLI output file read failed: {exc}")
        finally:
            try:
                os.unlink(output_file)
            except OSError:
                pass
    if result.returncode != 0:
        return None
    if not response_text:
        response_text = result.stdout or ""
    if not response_text:
        actions_log.append("Codex CLI returned empty output; ensure it is authenticated and configured.")
        return None
    try:
        payload = _parse_json_payload(response_text)
    except json.JSONDecodeError as exc:
        actions_log.append(f"Codex CLI returned invalid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        actions_log.append("Codex CLI returned non-dict payload; ignoring response.")
        return None
    payload["provider"] = payload.get("provider") or "codex"
    return payload


def _query_codex_plan(
    *,
    prompt: str,
    provider,
    system_prompt: str,
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
    model_override: Optional[str],
    actions_log: List[str],
) -> Optional[Dict[str, object]]:
    codex_system = (
        "You are Codex, a coding agent focused on producing high-quality code changes. "
        "Return ONLY JSON matching the schema provided. Prioritise correct, runnable code."
    )
    if system_prompt:
        codex_system = f"{codex_system}\n{system_prompt.strip()}"
    codex_user = (
        f"{prompt}\n\n"
        "Coding agent mode:\n"
        "- Focus on code changes and tests.\n"
        "- Keep plan steps minimal and implementable.\n"
        "- Avoid placeholders; ensure outputs are normalised to expected labels/units.\n"
    )
    try:
        original_model = getattr(provider, "model", None)
        if model_override:
            try:
                provider.model = model_override
            except Exception:
                pass
        resp = provider.predict(
            [{"role": "user", "content": codex_user}],
            system=codex_system,
            max_tokens=llm_max_tokens,
            temperature=min(0.2, llm_temperature),
            timeout=llm_timeout,
            reasoning_effort=llm_reasoning_effort,
        )
    except Exception as exc:
        actions_log.append(f"Codex plan request failed: {exc}")
        return None
    finally:
        if model_override and original_model is not None:
            try:
                provider.model = original_model
            except Exception:
                pass
    try:
        payload = _parse_json_payload(resp.text or "{}")
    except json.JSONDecodeError as exc:
        actions_log.append(f"Codex returned invalid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        actions_log.append("Codex returned non-dict payload; ignoring response.")
        return None
    payload["provider"] = payload.get("provider") or "codex"
    return payload


def _query_codingagent_plan(
    *,
    agent: str,
    prompt: str,
    provider,
    system_prompt: str,
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
    actions_log: List[str],
    workspace: str,
    output_path: str,
    allow_run: bool,
    llm_provider: Optional[str],
    llm_api_key: Optional[str],
    codingagent_model: Optional[str],
    codingagent_reasoning_effort: Optional[str],
) -> Optional[Dict[str, object]]:
    agent_norm = (agent or "").lower().strip()
    if agent_norm == "opencode":
        return _query_opencode_plan(
            prompt=prompt,
            workspace=workspace,
            output_path=output_path,
            allow_run=allow_run,
            actions_log=actions_log,
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
        )
    if agent_norm == "codex":
        cli_mode = _codex_cli_mode()
        effective_reasoning = _normalize_reasoning_effort(codingagent_reasoning_effort) or _normalize_reasoning_effort(llm_reasoning_effort)
        preflight = CODEX_PREFLIGHT_STATE or {}
        cli_ready = preflight.get("cli_ready")
        auth_ready = preflight.get("auth_ready")
        if cli_mode != "never":
            if cli_ready is False:
                actions_log.append("Codex CLI preflight failed; skipping CLI invocation.")
                if cli_mode == "force":
                    actions_log.append("Codex CLI mode forced; skipping provider fallback.")
                    return None
            elif auth_ready is False:
                actions_log.append("Codex CLI auth not detected; skipping CLI invocation.")
                if cli_mode == "force":
                    actions_log.append("Codex CLI mode forced; skipping provider fallback.")
                    return None
            else:
                payload = _query_codex_cli_plan(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    workspace=workspace,
                    allow_run=allow_run,
                    actions_log=actions_log,
                    llm_provider=llm_provider,
                    llm_api_key=llm_api_key,
                    model_override=codingagent_model,
                    reasoning_effort=effective_reasoning,
                )
                if payload:
                    return payload
                if cli_mode == "force":
                    actions_log.append("Codex CLI mode forced; skipping provider fallback.")
                    return None
        with request_category("codingagent"):
            return _query_codex_plan(
                prompt=prompt,
                provider=provider,
                system_prompt=system_prompt,
                llm_max_tokens=llm_max_tokens,
                llm_temperature=llm_temperature,
                llm_timeout=llm_timeout,
                llm_reasoning_effort=effective_reasoning,
                model_override=codingagent_model,
                actions_log=actions_log,
            )
    return None


def _summarize_tree(project_root: str, max_entries: int = 60, extra_ignored: Optional[List[str]] = None) -> str:
    entries = []
    ignored = _ignored_dirnames(extra_ignored)
    for root, dirs, files in os.walk(project_root):
        _filter_walk_dirs(root, dirs, ignored)
        rel_root = os.path.relpath(root, project_root)
        for filename in files:
            if len(entries) >= max_entries:
                break
            rel_path = os.path.normpath(os.path.join(rel_root, filename))
            entries.append(rel_path)
        if len(entries) >= max_entries:
            break
    return "\n".join(entries)


def _plan_recovery_steps(
    provider,
    failures: List[Dict[str, object]],
    *,
    workspace_note: str,
    venv_path: Optional[str],
    actions_log: List[str],
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
) -> List[Dict[str, object]]:
    failure_summary = _summarize_failures(failures)
    venv_note = f"Detected venv: {venv_path}. Prefer using its python/pip directly." if venv_path else "No venv detected."
    system_prompt = (
        "You are a senior engineer debugging failed shell commands. "
        "Provide a minimal, actionable recovery plan and include a retry of the failed command."
    )
    user_prompt = (
        "Command failures:\n"
        f"{failure_summary}\n\n"
        f"{workspace_note}\n"
        f"{venv_note}\n\n"
        "Recent actions (most recent):\n"
        f"{_trim_actions_log(actions_log)}\n\n"
        "Respond with JSON only. Schema:\n"
        "{\n"
        '  "summary": "short overview of fix",\n'
        '  "plan": [\n'
        "    {\n"
        '      "type": "note|create_dir|write_file|append_file|replace_in_file|run_command",\n'
        '      "step": "human readable step",\n'
        '      "path": "relative path for file operations (project root) or absolute path under solver workspace if outside project root",\n'
        '      "content": "file content for write/append",\n'
        '      "overwrite": true,\n'
        '      "find": "text to replace",\n'
        '      "replace": "replacement text",\n'
        '      "count": 0,\n'
        '      "command": "shell command",\n'
        '      "workdir": "relative dir (project root) or absolute dir under solver workspace if outside project root",\n'
        '      "timeout": 600\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Notes:\n"
        "- Avoid using 'source' or activation scripts as standalone commands; use venv/bin/python or venv/bin/pip directly.\n"
        "- Keep changes within the project root or solver workspace.\n"
        "- If failure output includes a FutureWarning, apply the suggested fix in the warning before retrying.\n"
        "- Include a retry of the failed command at the end of the plan.\n"
    )
    resp = provider.predict(
        [{"role": "user", "content": user_prompt}],
        system=system_prompt,
        max_tokens=llm_max_tokens,
        temperature=llm_temperature,
        timeout=llm_timeout,
        reasoning_effort=llm_reasoning_effort,
    )
    try:
        payload = _parse_json_payload(resp.text or "{}")
    except json.JSONDecodeError as exc:
        logger.info(f"Recovery plan JSON parse failed: {exc}")
        return []
    plan_steps = payload.get("plan", [])
    if not isinstance(plan_steps, list):
        return []
    return [step for step in plan_steps if isinstance(step, dict)]


def _requirements_specify_output_dir(requirements_text: str) -> bool:
    lowered = requirements_text.lower()
    tokens = (
        ".venv",
        "venv/",
        "./venv",
        "env/",
        "./env",
        "output/",
        "build/",
        "dist/",
        "workspace/",
        "workdir/",
        "work dir/",
        "work-dir/",
    )
    return any(token in lowered for token in tokens)


def _find_existing_output_dir_from_requirements(requirements_text: str, project_root: str) -> Optional[str]:
    if not requirements_text:
        return None
    # Absolute paths
    abs_candidates = re.findall(r"(/[^\s'\"`]+)", requirements_text)
    for candidate in abs_candidates:
        if os.path.isdir(candidate):
            return candidate

    # Relative path hints
    rel_candidates = re.findall(r"(?:^|\s)(\.?\.?/[\w\-.\/]+)", requirements_text)
    for candidate in rel_candidates:
        candidate_path = os.path.abspath(os.path.join(project_root, candidate))
        if os.path.isdir(candidate_path):
            return candidate_path

    # Token-based directory names
    lowered = requirements_text.lower()
    name_candidates = [
        "project_solver_output",
        "output",
        "workspace",
        "workdir",
        "build",
        "dist",
        ".venv",
        "venv",
        "env",
    ]
    for name in name_candidates:
        if name in lowered:
            candidate_path = os.path.join(project_root, name)
            if os.path.isdir(candidate_path):
                return candidate_path
    return None


def _looks_like_venv_dir(path: str) -> bool:
    return any(
        os.path.isfile(os.path.join(path, marker))
        for marker in ("pyvenv.cfg", os.path.join("bin", "activate"), os.path.join("Scripts", "activate"))
    )


def _is_path_under_venv(path: str) -> bool:
    abs_path = os.path.abspath(path)
    current = abs_path
    while True:
        if os.path.isfile(os.path.join(current, "pyvenv.cfg")):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return False


def _is_third_party_path(path: str) -> bool:
    norm = os.path.normpath(path)
    site_segments = (f"{os.sep}site-packages{os.sep}", f"{os.sep}dist-packages{os.sep}")
    if any(seg in norm for seg in site_segments):
        return True
    return _is_path_under_venv(norm)


def _source_is_under_root(source_path: str, root_path: str) -> bool:
    norm_source = os.path.normpath(source_path)
    norm_root = os.path.normpath(root_path)
    return norm_source == norm_root or norm_source.startswith(norm_root + os.sep)


def _apply_step(
    project_root: str,
    step: Dict[str, object],
    *,
    allow_run: bool,
    actions_log: List[str],
    allowed_roots: Optional[List[str]] = None,
    failure_log: Optional[List[Dict[str, object]]] = None,
    venv_path: Optional[str] = None,
    workspace_root: Optional[str] = None,
    hallucination_log: Optional[List[str]] = None,
    prefer_workspace_new_files: bool = False,
    applied_steps: Optional[List[Dict[str, object]]] = None,
    dataset_summary: Optional[Dict[str, object]] = None,
    eval_info: Optional[Dict[str, object]] = None,
) -> None:
    step_requirement_ids = _extract_requirement_refs_from_step(step)
    step_type = _normalize_step_type(step.get("type"))
    if not step_type:
        actions_log.append("Skipped step without type.")
        return

    if step_type == "note":
        note = step.get("step", "")
        actions_log.append(f"Note: {note}")
        if note:
            logger.info(f"Plan note: {note}")
        return

    if step_type == "create_dir":
        rel_path = step.get("path")
        if not isinstance(rel_path, str):
            actions_log.append("Skipped create_dir missing path.")
            return
        abs_path = _safe_path(project_root, rel_path, extra_roots=allowed_roots)
        if not abs_path:
            actions_log.append(f"Skipped unsafe path: {rel_path}")
            return
        if _is_third_party_path(abs_path):
            actions_log.append(f"Skipped create_dir under virtualenv/third-party path: {rel_path}")
            logger.info(f"Skipped create_dir (third-party path): {rel_path}")
            return
        if os.path.exists(abs_path) and not os.path.isdir(abs_path):
            actions_log.append(f"Skipped create_dir; path exists and is not a directory: {rel_path}")
            logger.info(f"Skipped create_dir (path is file): {rel_path}")
            return
        try:
            os.makedirs(abs_path, exist_ok=True)
            actions_log.append(f"Created directory: {rel_path}")
            logger.info(f"Created directory: {rel_path}")
        except Exception as exc:
            actions_log.append(f"Failed to create directory {rel_path}: {exc}")
            logger.info(f"Failed to create directory {rel_path}: {exc}")
        return

    if step_type in {"write_file", "append_file", "replace_in_file"}:
        rel_path = step.get("path")
        if not isinstance(rel_path, str):
            actions_log.append("Skipped file step missing path.")
            return
        if rel_path.endswith(("/", os.sep)):
            actions_log.append(f"Skipped file step; path looks like a directory: {rel_path}")
            logger.info(f"Skipped file step (directory-like path): {rel_path}")
            return
        target_path, redirect_note = _resolve_file_target(
            rel_path,
            project_root=project_root,
            workspace_root=workspace_root,
            step_type=step_type,
            prefer_workspace_new_files=prefer_workspace_new_files,
        )
        if redirect_note:
            actions_log.append(redirect_note)
            logger.info(redirect_note)
        abs_path = _safe_path(project_root, target_path, extra_roots=allowed_roots)
        if not abs_path:
            actions_log.append(f"Skipped unsafe path: {rel_path}")
            return
        if _is_third_party_path(abs_path):
            actions_log.append(f"Skipped file step under virtualenv/third-party path: {rel_path}")
            logger.info(f"Skipped file step (third-party path): {rel_path}")
            return
        if os.path.isdir(abs_path):
            actions_log.append(f"Skipped file step; path is a directory: {rel_path}")
            logger.info(f"Skipped file step (directory path): {rel_path}")
            return
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        if step_type == "write_file":
            content = step.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, indent=2)
            if abs_path.endswith(".py"):
                content = _normalize_python_indentation(content)
                pep8_fixed = _autopep8_format_text(content)
                if pep8_fixed:
                    content = pep8_fixed
            overwrite = step.get("overwrite", True)
            if os.path.exists(abs_path) and not overwrite:
                actions_log.append(f"Skipped existing file (overwrite=false): {rel_path}")
                logger.info(f"Skipped write_file (exists, overwrite=false): {rel_path}")
                return
            try:
                with open(abs_path, "w", encoding="utf-8") as handle:
                    handle.write(content)
                actions_log.append(f"Wrote file: {rel_path} (abs: {abs_path})")
                logger.info(f"Wrote file: {rel_path} (abs: {abs_path})")
                if abs_path.endswith(".py"):
                    try:
                        ast.parse(content)
                    except SyntaxError as exc:
                        msg = f"SyntaxError in {abs_path}: {exc}"
                        actions_log.append(msg)
                        logger.info(msg)
                        if failure_log is not None:
                            failure_log.append(
                                {
                                    "command": f"syntax_check:{abs_path}",
                                    "workdir": os.path.dirname(abs_path) or ".",
                                    "exit_code": None,
                                    "stdout": "",
                                    "stderr": msg,
                                    "verification_issue": "syntax error",
                                }
                            )
                if applied_steps is not None:
                    applied_steps.append(
                        {
                            "type": "write_file",
                            "path": rel_path,
                            "abs_path": abs_path,
                            "line": 1,
                            "note": "write_file",
                            "is_code": os.path.splitext(rel_path)[1].lower() in CODE_FILE_EXTS
                            or bool(TEST_FILE_RE.search(rel_path)),
                            "requirement_ids": step_requirement_ids,
                        }
                    )
                if _is_requirements_file(abs_path):
                    treat_missing = bool(workspace_root and _is_subpath(workspace_root, abs_path))
                    _update_requirements_versions(
                        abs_path,
                        actions_log,
                        treat_missing_as_hallucination=treat_missing,
                        hallucination_log=hallucination_log,
                    )
            except Exception as exc:
                actions_log.append(f"Failed to write file {rel_path}: {exc}")
                logger.info(f"Failed to write file {rel_path}: {exc}")
            return

        if step_type == "append_file":
            content = step.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, indent=2)
            if abs_path.endswith(".py"):
                content = _normalize_python_indentation(content)
                pep8_fixed = _autopep8_format_text(content)
                if pep8_fixed:
                    content = pep8_fixed
            try:
                start_line = 1
                existing = _read_text_file(abs_path, max_bytes=5_000_000)
                if existing is not None:
                    start_line = existing.count("\n") + 1
                with open(abs_path, "a", encoding="utf-8") as handle:
                    handle.write(content)
                actions_log.append(f"Appended file: {rel_path} (abs: {abs_path})")
                logger.info(f"Appended file: {rel_path} (abs: {abs_path})")
                if abs_path.endswith(".py"):
                    combined = _read_text_file(abs_path, max_bytes=5_000_000) or ""
                    try:
                        ast.parse(combined)
                    except SyntaxError as exc:
                        msg = f"SyntaxError in {abs_path}: {exc}"
                        actions_log.append(msg)
                        logger.info(msg)
                        if failure_log is not None:
                            failure_log.append(
                                {
                                    "command": f"syntax_check:{abs_path}",
                                    "workdir": os.path.dirname(abs_path) or ".",
                                    "exit_code": None,
                                    "stdout": "",
                                    "stderr": msg,
                                    "verification_issue": "syntax error",
                                }
                            )
                if applied_steps is not None:
                    applied_steps.append(
                        {
                            "type": "append_file",
                            "path": rel_path,
                            "abs_path": abs_path,
                            "line": start_line,
                            "note": "append_file",
                            "is_code": os.path.splitext(rel_path)[1].lower() in CODE_FILE_EXTS
                            or bool(TEST_FILE_RE.search(rel_path)),
                            "requirement_ids": step_requirement_ids,
                        }
                    )
                if _is_requirements_file(abs_path):
                    treat_missing = bool(workspace_root and _is_subpath(workspace_root, abs_path))
                    _update_requirements_versions(
                        abs_path,
                        actions_log,
                        treat_missing_as_hallucination=treat_missing,
                        hallucination_log=hallucination_log,
                    )
            except Exception as exc:
                actions_log.append(f"Failed to append file {rel_path}: {exc}")
                logger.info(f"Failed to append file {rel_path}: {exc}")
            return

        if step_type == "replace_in_file":
            find_text = step.get("find")
            replace_text = step.get("replace", "")
            if not isinstance(find_text, str) or not find_text:
                actions_log.append(f"Skipped replace_in_file without find: {rel_path}")
                logger.info(f"Skipped replace_in_file (missing find): {rel_path}")
                return
            existing = _read_text_file(abs_path, max_bytes=5_000_000)
            if existing is None:
                actions_log.append(f"Skipped replace_in_file unreadable: {rel_path}")
                logger.info(f"Skipped replace_in_file (unreadable): {rel_path}")
                return
            count = int(step.get("count", 0)) or -1
            if find_text not in existing:
                actions_log.append(f"Skipped replace_in_file; pattern not found in {rel_path}")
                logger.info(f"Skipped replace_in_file (pattern not found): {rel_path}")
                return
            updated = existing.replace(find_text, replace_text, count if count > 0 else existing.count(find_text))
            if abs_path.endswith(".py"):
                updated = _normalize_python_indentation(updated)
                pep8_fixed = _autopep8_format_text(updated)
                if pep8_fixed:
                    updated = pep8_fixed
            try:
                with open(abs_path, "w", encoding="utf-8") as handle:
                    handle.write(updated)
                actions_log.append(f"Replaced text in file: {rel_path} (abs: {abs_path})")
                logger.info(f"Replaced text in file: {rel_path} (abs: {abs_path})")
                if abs_path.endswith(".py"):
                    try:
                        ast.parse(updated)
                    except SyntaxError as exc:
                        msg = f"SyntaxError in {abs_path}: {exc}"
                        actions_log.append(msg)
                        logger.info(msg)
                        if failure_log is not None:
                            failure_log.append(
                                {
                                    "command": f"syntax_check:{abs_path}",
                                    "workdir": os.path.dirname(abs_path) or ".",
                                    "exit_code": None,
                                    "stdout": "",
                                    "stderr": msg,
                                    "verification_issue": "syntax error",
                                }
                            )
                if applied_steps is not None:
                    target_text = replace_text if isinstance(replace_text, str) else ""
                    if not target_text:
                        target_text = find_text
                    applied_steps.append(
                        {
                            "type": "replace_in_file",
                            "path": rel_path,
                            "abs_path": abs_path,
                            "line": _line_number_for_text(updated, target_text),
                            "note": "replace_in_file",
                            "is_code": os.path.splitext(rel_path)[1].lower() in CODE_FILE_EXTS
                            or bool(TEST_FILE_RE.search(rel_path)),
                            "requirement_ids": step_requirement_ids,
                        }
                    )
                if _is_requirements_file(abs_path):
                    treat_missing = bool(workspace_root and _is_subpath(workspace_root, abs_path))
                    _update_requirements_versions(
                        abs_path,
                        actions_log,
                        treat_missing_as_hallucination=treat_missing,
                        hallucination_log=hallucination_log,
                    )
            except Exception as exc:
                actions_log.append(f"Failed to replace text in {rel_path}: {exc}")
                logger.info(f"Failed to replace text in {rel_path}: {exc}")
            return

    if step_type == "run_command":
        command = step.get("command")
        if not isinstance(command, str):
            actions_log.append("Skipped run_command missing command.")
            logger.info("Skipped run_command (missing command).")
            return
        if not allow_run:
            actions_log.append(f"Skipped run_command (disabled): {command}")
            logger.info(f"Skipped run_command (disabled): {command}")
            return
        if _is_activation_command(command, venv_path):
            actions_log.append("Skipped activation command; using venv python/pip directly.")
            logger.info("Skipped activation command; using venv python/pip directly.")
            return
        workdir_value = step.get("workdir")
        if isinstance(workdir_value, str) and workdir_value.strip():
            workdir = workdir_value.strip()
        else:
            workdir = "."
        abs_workdir = _safe_path(project_root, workdir, extra_roots=allowed_roots)
        if not abs_workdir:
            actions_log.append(f"Skipped run_command unsafe workdir: {workdir}")
            logger.info(f"Skipped run_command (unsafe workdir): {workdir}")
            return
        sanitized_command = _sanitize_shell_command(command, venv_path)
        rewritten_command = _rewrite_command_for_venv(sanitized_command, venv_path)
        rewritten_command = _rewrite_requirements_path_in_command(
            rewritten_command,
            abs_workdir=abs_workdir,
            workspace_root=workspace_root,
            actions_log=actions_log,
        )
        try:
            timeout = step.get("timeout", 600)
            logger.info(f"Executing command: {rewritten_command} (workdir={workdir})")
            result = subprocess.run(
                rewritten_command,
                cwd=abs_workdir,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout if isinstance(timeout, (int, float)) else 600,
            )
            logger.info(f"Command exit code: {result.returncode}")
            actions_log.append(
                f"Ran command: {rewritten_command}\nExit code: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
            verification_issue = None
            if _is_verification_command(rewritten_command):
                verification_issue = _verification_output_issue(
                    result.stdout,
                    result.stderr,
                    dataset_summary=dataset_summary,
                    eval_info=eval_info,
                )
                if verification_issue:
                    actions_log.append(
                        f"Verification output issue detected ({verification_issue}); treating as failure."
                    )
            if result.returncode == 0 and not verification_issue:
                actions_log.append(f"Command succeeded: {rewritten_command}")
            else:
                actions_log.append(f"Command failed (exit {result.returncode}): {rewritten_command}")
                if failure_log is not None:
                    failure_log.append(
                        {
                            "command": rewritten_command,
                            "workdir": abs_workdir,
                            "exit_code": result.returncode,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                            "verification_issue": verification_issue,
                        }
                    )
        except Exception as exc:
            actions_log.append(f"Command failed: {rewritten_command} ({exc})")
            logger.info(f"Command failed: {rewritten_command} ({exc})")
            if failure_log is not None:
                failure_log.append(
                    {
                        "command": rewritten_command,
                        "workdir": abs_workdir,
                        "exit_code": None,
                        "stdout": "",
                        "stderr": str(exc),
                    }
                )
        return

    actions_log.append(f"Skipped unknown step type: {step_type}")


def run_project_solver(
    project_root: str,
    *,
    requirements_path: Optional[str],
    output_path: str,
    llm_provider: str,
    llm_model: Optional[str],
    ollama_base_url: Optional[str],
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
    llm_api_key: Optional[str],
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    fallback_llm_api_key: Optional[str] = None,
    llm_inter_request_gap: float = 0.0,
    allow_run: bool = False,
    max_steps: int = 25,
    max_iterations: int = 3,
    project_output_dir: Optional[str] = None,
    codingagent: Optional[str] = None,
    codingagent_fallback: Optional[str] = None,
    codingagent_model: Optional[str] = None,
    codingagent_reasoning_effort: Optional[str] = None,
    agentic_roles: Optional[Dict[str, Dict[str, object]]] = None,
) -> int:
    """
    Run the project solver for a local project folder.

    Iteratively requests plans from the LLM, applies steps per requirement
    source, and writes a JSON report with the plan history and a log of applied
    actions. An optional project_output_dir overrides the default solver
    workspace and can be an absolute path outside the project root.
    """
    if not os.path.isdir(project_root):
        raise ValueError(f"Project root is not a directory: {project_root}")

    actions_log: List[str] = []
    provider = get_provider(
        llm_provider,
        model=llm_model,
        base_url=ollama_base_url,
        inter_request_gap=llm_inter_request_gap,
        api_key=llm_api_key,
    )
    if not provider:
        raise ValueError("No LLM provider available for project solving.")

    fallback_provider = None
    if fallback_llm_provider:
        f_kwargs: Dict[str, object] = {}
        if fallback_llm_api_key:
            if fallback_llm_provider in ("gemini", "google") and fallback_llm_api_key.startswith("ya29."):
                f_kwargs["access_token"] = fallback_llm_api_key
            else:
                f_kwargs["api_key"] = fallback_llm_api_key
        try:
            fallback_provider = get_provider(
                fallback_llm_provider,
                model=fallback_llm_model,
                base_url=ollama_base_url,
                inter_request_gap=llm_inter_request_gap,
                **f_kwargs,
            )
            actions_log.append(
                f"Initialized fallback LLM provider: {getattr(fallback_provider, 'name', fallback_llm_provider)}"
            )
        except Exception as exc:
            actions_log.append(f"Failed to initialize fallback LLM provider {fallback_llm_provider}: {exc}")
            fallback_provider = None
    if fallback_provider:
        provider = _FallbackProvider(provider, fallback_provider, actions_log)

    role_configs: Dict[str, Dict[str, object]] = {}
    if isinstance(agentic_roles, dict):
        for role, cfg in agentic_roles.items():
            if isinstance(cfg, dict):
                role_configs[str(role).strip().lower()] = dict(cfg)
    if "reviewer" not in role_configs and "audit" in role_configs:
        role_configs["reviewer"] = role_configs["audit"]
    if "planner" not in role_configs and "plan" in role_configs:
        role_configs["planner"] = role_configs["plan"]

    def _role_params(role_name: str) -> Dict[str, object]:
        cfg = role_configs.get(role_name, {})
        max_tokens = cfg.get("max_tokens")
        temperature = cfg.get("temperature")
        timeout = cfg.get("timeout")
        reasoning_effort = cfg.get("reasoning_effort")
        return {
            "max_tokens": llm_max_tokens if max_tokens is None else max_tokens,
            "temperature": llm_temperature if temperature is None else temperature,
            "timeout": llm_timeout if timeout is None else timeout,
            "reasoning_effort": llm_reasoning_effort if reasoning_effort is None else reasoning_effort,
        }

    def _build_role_provider(role_name: str):
        cfg = role_configs.get(role_name)
        if not isinstance(cfg, dict) or not cfg:
            return provider
        provider_type = cfg.get("provider") or cfg.get("type") or cfg.get("llm_provider")
        if not provider_type:
            return provider
        model = cfg.get("model") or llm_model
        base_url = cfg.get("base_url") or ollama_base_url
        api_key = cfg.get("api_key")
        kwargs = {}
        if api_key:
            if provider_type in ("gemini", "google") and str(api_key).startswith("ya29."):
                kwargs["access_token"] = api_key
            else:
                kwargs["api_key"] = api_key
        try:
            role_provider = get_provider(
                provider_type,
                model=model,
                base_url=base_url,
                inter_request_gap=llm_inter_request_gap,
                **kwargs,
            )
        except Exception as exc:
            actions_log.append(f"Failed to init role provider '{role_name}': {exc}")
            return provider
        if fallback_provider:
            role_provider = _FallbackProvider(role_provider, fallback_provider, actions_log)
        return role_provider

    planner_provider = _build_role_provider("planner")
    reviewer_provider = _build_role_provider("reviewer")
    researcher_provider = _build_role_provider("researcher")
    planner_params = _role_params("planner")
    reviewer_params = _role_params("reviewer")
    researcher_params = _role_params("researcher")

    requirement_sources: List[RequirementSource] = []
    context_summary = ""
    codingagent_primary = _normalize_codingagent(codingagent)
    codingagent_fallback_norm = _normalize_codingagent(codingagent_fallback or "llm")
    codingagent_primary_mode = _codingagent_primary_mode()
    audit_every_iterations = _env_int("SOLVER_AUDIT_EVERY_ITERATIONS", 5)
    audit_every_steps = _env_int("SOLVER_AUDIT_EVERY_STEPS", 50)
    audit_agent_mode = (os.getenv("SOLVER_AUDIT_AGENT", "auto") or "auto").strip().lower()
    audit_enabled = audit_every_iterations > 0 or audit_every_steps > 0
    audit_max_chars = _env_int("SOLVER_AUDIT_MAX_CHARS", 1200)
    audit_log_path = os.getenv("SOLVER_AUDIT_LOG_PATH", "rag_demo.log") or ""
    web_research_mode = os.getenv("SOLVER_WEB_RESEARCH", "auto") or "auto"
    web_research_every_iterations = _env_int("SOLVER_WEB_RESEARCH_EVERY_ITERATIONS", 4)
    web_research_every_steps = _env_int("SOLVER_WEB_RESEARCH_EVERY_STEPS", 60)
    web_research_max_queries = _env_int("SOLVER_WEB_RESEARCH_MAX_QUERIES", 2)
    web_research_max_results = _env_int("SOLVER_WEB_RESEARCH_MAX_RESULTS", 3)
    web_research_fetch_timeout = _env_int("SOLVER_WEB_RESEARCH_FETCH_TIMEOUT", 20)
    web_research_fetch_max_bytes = _env_int("SOLVER_WEB_RESEARCH_FETCH_MAX_BYTES", 200_000)
    web_research_cache_ttl_hours = _env_int("SOLVER_WEB_RESEARCH_CACHE_TTL_HOURS", 24)
    search_engines = _load_search_engines(project_root, llm_timeout, web_research_cache_ttl_hours)
    web_research_enabled = _web_research_enabled(web_research_mode, search_engines)
    if web_research_enabled and not search_engines:
        actions_log.append(
            "Web research enabled but no Google search engines configured; skipping external search."
        )
    solver_workspace_rel: Optional[str] = None
    if codingagent_primary or codingagent_fallback_norm:
        actions_log.append(
            f"Coding agent configured: primary={codingagent_primary or 'llm'}, "
            f"fallback={codingagent_fallback_norm or 'llm'}, "
            f"mode={codingagent_primary_mode}"
        )
    codex_preflight = _codex_preflight(
        allow_run=allow_run,
        actions_log=actions_log,
        codingagent_primary=codingagent_primary,
        codingagent_fallback=codingagent_fallback_norm,
    )
    solver_workspace: Optional[str] = None
    extra_ignored: List[str] = []
    output_dir_explicit = False
    output_dir_implied = False
    solver_workspace_within_project = False
    prefer_workspace_new_files = False
    if project_output_dir:
        output_dir_explicit = True
        if os.path.isabs(project_output_dir):
            abs_workspace = os.path.abspath(project_output_dir)
            solver_workspace_within_project = _is_subpath(project_root, abs_workspace)
        else:
            abs_workspace = os.path.abspath(os.path.join(project_root, project_output_dir))
            solver_workspace_within_project = _is_subpath(project_root, abs_workspace)
            if not solver_workspace_within_project:
                raise ValueError("Relative project output dir must be inside the project root.")
        solver_workspace = abs_workspace
        solver_workspace_rel = os.path.relpath(abs_workspace, project_root) if solver_workspace_within_project else abs_workspace
        if solver_workspace_within_project:
            extra_ignored.append(solver_workspace_rel)
    if requirements_path:
        converter = FileConverter()
        requirements_text = converter.convert(requirements_path)
        if requirements_text.startswith("Error:"):
            raise ValueError(requirements_text)
        context_summary = "Requirements provided directly; no project scan performed."
        excerpt = "\n".join(requirements_text.splitlines()[:40]).strip()
        requirement_sources = [
            RequirementSource(
                path=requirements_path,
                requirements_text=requirements_text,
                requirement_lines=[],
                todo_lines=[],
                context_excerpt=excerpt,
            )
        ]
    else:
        scan_result = scan_project_requirements(project_root, extra_ignored=extra_ignored)
        requirement_sources = scan_result.requirements_by_source
        context_summary = scan_result.context_summary
        if not requirement_sources:
            requirement_sources = [
                RequirementSource(
                    path="project scan",
                    requirements_text="No explicit requirement statements found; infer intent from project context.",
                    requirement_lines=[],
                    todo_lines=[],
                    context_excerpt=context_summary,
                )
            ]

    requirements_material = "\n\n".join(src.requirements_text for src in requirement_sources if src.requirements_text).strip()
    if not requirements_material:
        requirements_material = context_summary or "No explicit requirement statements found."

    if not solver_workspace_rel:
        implied_workspace = _find_existing_output_dir_from_requirements(requirements_material, project_root)
        if implied_workspace:
            solver_workspace = implied_workspace
            solver_workspace_within_project = _is_subpath(project_root, solver_workspace)
            solver_workspace_rel = (
                os.path.relpath(implied_workspace, project_root)
                if solver_workspace_within_project
                else implied_workspace
            )
            if solver_workspace_within_project:
                extra_ignored.append(solver_workspace_rel)
                requirement_sources = [
                    src
                    for src in requirement_sources
                    if not _source_is_under_root(src.path, solver_workspace_rel)
                ]
            output_dir_implied = True
            actions_log.append(f"Using output dir implied by requirements: {solver_workspace_rel}")
        elif _requirements_specify_output_dir(requirements_material):
            actions_log.append("Requirements specify output location; no solver workspace created.")
        else:
            solver_workspace_rel = DEFAULT_SOLVER_OUTPUT_DIR
            solver_workspace = os.path.join(project_root, solver_workspace_rel)
            solver_workspace_within_project = True

    if solver_workspace and (output_dir_explicit or not _requirements_specify_output_dir(requirements_material)):
        prefer_workspace_new_files = True

    workspace_preexisting = bool(solver_workspace and os.path.isdir(solver_workspace))
    if solver_workspace_rel and solver_workspace:
        os.makedirs(solver_workspace, exist_ok=True)
        if workspace_preexisting:
            actions_log.append(f"Using existing solver workspace: {solver_workspace_rel}")
            logger.info(f"Using existing solver workspace: {solver_workspace_rel}")
        else:
            actions_log.append(f"Created solver workspace: {solver_workspace_rel}")
            logger.info(f"Created solver workspace: {solver_workspace_rel}")

    workspace_sources: List[RequirementSource] = []
    if solver_workspace and os.path.isdir(solver_workspace):
        if _looks_like_venv_dir(solver_workspace):
            actions_log.append("Solver workspace appears to be a virtual environment; skipping requirement scan there.")
        else:
            workspace_display = solver_workspace_rel if solver_workspace_within_project else solver_workspace
            workspace_sources, _ = _scan_requirements_root(
                solver_workspace,
                root_display=workspace_display,
                max_files=40,
                max_file_bytes=200_000,
                max_context_files=6,
                max_code_files=80,
                extra_ignored=None,
            )
            if workspace_sources:
                requirement_sources.extend(workspace_sources)
                actions_log.append(
                    f"Discovered {len(workspace_sources)} requirement sources in solver workspace."
                )

    sequence_gaps = _detect_sequence_gaps(project_root, extra_ignored=extra_ignored)
    if sequence_gaps:
        for gap in sequence_gaps:
            prefix = _safe_str(gap.get("prefix"))
            missing = gap.get("missing") if isinstance(gap.get("missing"), list) else []
            if prefix and missing:
                actions_log.append(
                    f"Sequence gap detected for '{prefix}': missing {', '.join(str(num) for num in missing)}"
                )

    sample_code_map = _collect_sample_code_map(project_root, extra_ignored=extra_ignored)
    if sample_code_map:
        actions_log.append(f"Discovered {len(sample_code_map)} sample code file(s) for reference.")

    repo_rag_enabled = _env_bool("SOLVER_REPO_RAG", True)
    repo_index: Optional[RepoIndex] = None
    if repo_rag_enabled:
        try:
            repo_index = RepoIndex.build(
                project_root,
                extra_ignored=extra_ignored,
                max_files=_env_int("SOLVER_REPO_RAG_MAX_FILES", 300),
                max_file_bytes=_env_int("SOLVER_REPO_RAG_MAX_BYTES", 200_000),
            )
            stats = repo_index.stats()
            actions_log.append(
                f"Repo context index built: {stats.get('file_count', 0)} files "
                f"({stats.get('code_files', 0)} code)."
            )
        except Exception as exc:
            actions_log.append(f"Repo context index failed: {exc}")
            repo_index = None

    dataset_summary = _collect_dataset_summary(project_root, extra_ignored=extra_ignored)
    if dataset_summary.get("count"):
        actions_log.append(
            f"Dataset detected: {dataset_summary['count']} file(s) under data/."
        )
    eval_info = _collect_eval_data_schema(project_root)
    if eval_info.get("count"):
        actions_log.append(
            f"Eval data detected: {eval_info.get('count')} record(s) in eval_data.json."
        )
    helper_modules = _collect_helper_module_summaries(project_root, extra_ignored=extra_ignored)
    if helper_modules:
        actions_log.append(
            f"Helper modules detected: {', '.join(sorted(helper_modules.keys()))}"
        )

    requirements_register, requirements_register_source = _build_requirements_register(
        requirement_sources,
        context_summary,
        provider,
        llm_max_tokens=llm_max_tokens,
        llm_temperature=llm_temperature,
        llm_timeout=llm_timeout,
        llm_reasoning_effort=llm_reasoning_effort,
        actions_log=actions_log,
    )
    requirements_register = _ensure_sequence_requirements(
        requirements_register,
        sequence_gaps,
        requirement_sources=requirement_sources,
    )
    requirements_register = _ensure_dataset_requirements(
        requirements_register,
        dataset_summary,
        requirements_material,
        requirement_sources=requirement_sources,
    )
    requirements_register = _ensure_eval_schema_requirements(
        requirements_register,
        eval_info,
        requirement_sources=requirement_sources,
    )
    requirements_register = _ensure_accuracy_strategy_requirements(
        requirements_register,
        requirements_material,
        requirement_sources=requirement_sources,
    )
    requirements_register = _ensure_helper_module_requirements(
        requirements_register,
        helper_modules,
        requirement_sources=requirement_sources,
    )
    requirements_register_markdown = _format_requirements_register_markdown(requirements_register)
    requirements_register_list = requirements_register.get("requirements")
    if not isinstance(requirements_register_list, list):
        requirements_register_list = []
    requirements_register_index = _format_requirements_register_index(requirements_register_list)
    requirements_register_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements_register_list
        if isinstance(item, dict) and _safe_str(item.get("id"))
    }
    requirements_register_sequence_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements_register_list
        if isinstance(item, dict)
        and _safe_str(item.get("id"))
        and ("sequence_check" in _coerce_list(item.get("source")))
    }
    requirements_register_global_ids = {
        _safe_str(item.get("id")).upper()
        for item in requirements_register_list
        if isinstance(item, dict)
        and _safe_str(item.get("id"))
        and (
            ("global" in _coerce_list(item.get("source")))
            or ("global requirement" in _safe_str(item.get("notes")).lower())
        )
    }
    actions_log.append(
        f"Formal requirements register generated ({requirements_register_source}; count={len(requirements_register_list)})."
    )

    previous_run = _load_previous_output(output_path)
    previous_actions_log: List[str] = []
    previous_plans: List[object] = []
    previous_source_logs: Dict[str, List[str]] = {}
    completed_sources: List[str] = []
    plan_state: Dict[str, Dict[str, object]] = {}
    previous_progress_entries: List[Dict[str, object]] = []
    resume_source_path: Optional[str] = None
    resume_start_iteration: Dict[str, int] = {}
    resume_used = False
    if previous_run:
        prev_config = previous_run.get("run_config") if isinstance(previous_run, dict) else None
        prev_root = None
        if isinstance(prev_config, dict):
            prev_root = prev_config.get("project_root")
        if not prev_root or os.path.abspath(prev_root) == os.path.abspath(project_root):
            previous_actions_log = previous_run.get("actions_log", []) if isinstance(previous_run.get("actions_log"), list) else []
            previous_plans = previous_run.get("plans", []) if isinstance(previous_run.get("plans"), list) else []
            previous_source_logs = _extract_source_logs(previous_actions_log)
            completed_sources = _extract_completed_sources(previous_actions_log)
            plan_state = _extract_plan_state(previous_plans)
            prev_progress = previous_run.get("progress_tracker") if isinstance(previous_run, dict) else None
            if isinstance(prev_progress, dict) and isinstance(prev_progress.get("entries"), list):
                previous_progress_entries = prev_progress.get("entries") or []
            for source_path, entry in plan_state.items():
                if entry.get("done") is True:
                    completed_sources.append(source_path)
            current_source_paths = [src.path for src in requirement_sources]
            pending_sources = [p for p in current_source_paths if p not in completed_sources]
            if pending_sources:
                resume_source_path = pending_sources[0]
                if resume_source_path in plan_state:
                    resume_start_iteration[resume_source_path] = int(plan_state[resume_source_path].get("last_iteration", 0)) + 1
            else:
                prev_max_iter = prev_config.get("max_iterations") if isinstance(prev_config, dict) else None
                if isinstance(prev_max_iter, int) and max_iterations > prev_max_iter:
                    last_plan_source = None
                    for plan in reversed(previous_plans):
                        if isinstance(plan, dict) and isinstance(plan.get("source_path"), str):
                            last_plan_source = plan.get("source_path")
                            break
                    if last_plan_source in current_source_paths:
                        resume_source_path = last_plan_source
                    elif current_source_paths:
                        resume_source_path = current_source_paths[-1]
                    if resume_source_path:
                        resume_start_iteration[resume_source_path] = int(plan_state.get(resume_source_path, {}).get("last_iteration", 0)) + 1
            if resume_source_path:
                resume_used = True
                actions_log.append(f"Resuming from previous output: {output_path}")
    if previous_actions_log:
        actions_log = list(previous_actions_log) + actions_log
    progress_tracker = ProgressTracker(
        label="project_solver",
        logger=logger,
        entries=previous_progress_entries,
    )
    progress_last_id_by_source: Dict[str, int] = {}
    progress_checkpoint_by_source: Dict[str, int] = {}
    for entry in progress_tracker.entries:
        if not entry.source:
            continue
        progress_last_id_by_source[entry.source] = entry.id
        if entry.status not in {"dead_end", "deferred"}:
            progress_checkpoint_by_source[entry.source] = entry.id

    system_prompt = (
        "You are a senior software engineer acting as a project solver. "
        "Given project context and requirements, produce a concise, actionable plan and the exact steps needed to implement it. "
        "Return ONLY JSON matching the schema provided."
    )
    allowed_roots: List[str] = []
    if solver_workspace and not solver_workspace_within_project:
        allowed_roots.append(solver_workspace)
    all_plans: List[Dict[str, object]] = []
    if previous_plans:
        all_plans.extend([p for p in previous_plans if isinstance(p, dict)])
    total_steps_applied = 0
    workspace_has_sources = bool(workspace_sources)
    hallucinations_by_source: Dict[str, List[str]] = {}
    inadequate_counts: Dict[str, int] = {}
    opencode_used_sources: set = set()
    codingagent_used_sources: Dict[str, set] = {}
    applied_steps_by_source: Dict[str, List[Dict[str, object]]] = {}
    unresolved_failures: List[Dict[str, object]] = []
    audit_reports: List[Dict[str, object]] = []
    audit_notes_by_source: Dict[str, str] = {}
    audit_required_by_source: Dict[str, bool] = {}
    blocked_audit_streak_by_source: Dict[str, int] = {}
    last_audit_iteration_by_source: Dict[str, int] = {}
    last_audit_steps_by_source: Dict[str, int] = {}
    web_research_reports: List[Dict[str, object]] = []
    web_research_notes_by_source: Dict[str, str] = {}
    last_web_research_iteration_by_source: Dict[str, int] = {}
    last_web_research_steps_by_source: Dict[str, int] = {}
    verification_failures_by_source: Dict[str, List[Dict[str, object]]] = {}
    agentic_workflow = AgenticWorkflow(
        phases=["plan", "act", "verify", "reflect"],
        max_cycles=max_iterations,
        logger=logger,
        label="project_solver",
    )

    def _record_action(message: str, local_log: Optional[List[str]] = None) -> None:
        actions_log.append(message)
        if local_log is not None:
            local_log.append(message)

    source_count = len(requirement_sources)
    source_index_map = {src.path: idx + 1 for idx, src in enumerate(requirement_sources)}
    completed_set = set(completed_sources)
    source_queue: List[RequirementSource] = list(requirement_sources)
    deferred_once: set = set()
    audit_blocked_limit = _env_int("SOLVER_AUDIT_BLOCKED_LIMIT", 2)
    while source_queue:
        source = source_queue.pop(0)
        if resume_used and source.path in completed_set and source.path != resume_source_path:
            actions_log.append(f"Skipping completed requirement source from previous run: {source.path}")
            continue
        actions_log.append(f"Starting requirement source: {source.path}")
        source_actions_log: List[str] = list(previous_source_logs.get(source.path, []))
        source_applied_steps = applied_steps_by_source.setdefault(source.path, [])
        source_hallucinations = hallucinations_by_source.setdefault(source.path, [])
        source_requires_code = _source_requires_code(source)
        source_required_ids = (
            _required_ids_for_source(requirements_register_list, source.path)
            if isinstance(requirements_register_list, list)
            else set()
        )
        audit_note = audit_notes_by_source.get(source.path, "")
        audit_required = audit_required_by_source.get(source.path, False)
        web_research_note = web_research_notes_by_source.get(source.path, "")
        start_iteration = resume_start_iteration.get(source.path, 1)
        if resume_used and source.path == resume_source_path and start_iteration > 1:
            actions_log.append(
                f"Resuming requirement source: {source.path} at iteration {start_iteration}"
            )
        if start_iteration > max_iterations:
            actions_log.append(f"Skipping source {source.path}; iterations already exhausted.")
            continue
        defer_source = False
        last_iteration = start_iteration - 1
        for iteration in range(start_iteration, max_iterations + 1):
            last_iteration = iteration
            if total_steps_applied >= max_steps:
                _record_action("Max steps reached; stopping iterations.", source_actions_log)
                break
            cycle = agentic_workflow.start_cycle(iteration, context={"source": source.path})
            if audit_enabled:
                last_iter = last_audit_iteration_by_source.get(source.path, 0)
                last_step = last_audit_steps_by_source.get(source.path, 0)
                due_by_iter = audit_every_iterations > 0 and (iteration - last_iter) >= audit_every_iterations
                due_by_step = audit_every_steps > 0 and (total_steps_applied - last_step) >= audit_every_steps
                if (due_by_iter or due_by_step) and (source_actions_log or iteration > 1):
                    audit_agent = _select_audit_agent(
                        audit_agent_mode,
                        source_requires_code=source_requires_code,
                        codingagent_primary=codingagent_primary,
                    )
                    model_override = codingagent_model if audit_agent == "codingagent" else None
                    audit_label = f"{audit_agent} audit"
                    actions_log.append(
                        f"Running third-party {audit_label} for {source.path} (iteration {iteration})."
                    )
                    log_excerpt = _read_log_tail(project_root, audit_log_path)
                    with request_category("codingagent" if audit_agent == "codingagent" else "llm"):
                        audit_payload = _run_third_party_audit(
                            provider=reviewer_provider,
                            source=source,
                            requirements_register=requirements_register,
                            requirements_register_index=requirements_register_index,
                            source_actions_log=source_actions_log,
                            applied_steps=source_applied_steps,
                            project_root=project_root,
                            log_excerpt=log_excerpt,
                            llm_max_tokens=reviewer_params.get("max_tokens"),
                            llm_temperature=reviewer_params.get("temperature", llm_temperature),
                            llm_timeout=reviewer_params.get("timeout"),
                            llm_reasoning_effort=reviewer_params.get("reasoning_effort"),
                            model_override=model_override,
                            actions_log=actions_log,
                        )
                    if audit_payload:
                        audit_reports.append(
                            {
                                "source": source.path,
                                "iteration": iteration,
                                "agent": audit_agent,
                                "payload": audit_payload,
                            }
                        )
                        audit_note = _format_audit_report(audit_payload, max_chars=audit_max_chars)
                        audit_notes_by_source[source.path] = audit_note
                        audit_status = _safe_str(audit_payload.get("status")).lower()
                        audit_required = (
                            audit_status in {"drift", "blocked"}
                            or bool(audit_payload.get("realign_steps"))
                            or bool(audit_payload.get("scope_creep"))
                            or bool(audit_payload.get("missing_requirements"))
                        )
                        if audit_status == "blocked":
                            blocked_audit_streak_by_source[source.path] = (
                                blocked_audit_streak_by_source.get(source.path, 0) + 1
                            )
                            if audit_blocked_limit > 0 and blocked_audit_streak_by_source[source.path] > audit_blocked_limit:
                                audit_required = False
                                blocked_audit_streak_by_source[source.path] = 0
                                actions_log.append(
                                    f"Audit blocked loop breaker: exceeded {audit_blocked_limit} consecutive blocked audits for {source.path}. "
                                    "Continuing with last known plan signals."
                                )
                        else:
                            blocked_audit_streak_by_source[source.path] = 0
                        audit_required_by_source[source.path] = audit_required
                        last_audit_iteration_by_source[source.path] = iteration
                        last_audit_steps_by_source[source.path] = total_steps_applied
                        _record_action(
                            f"Third-party audit status: {audit_status or 'unknown'}.",
                            source_actions_log,
                        )
            if web_research_enabled and search_engines:
                last_iter = last_web_research_iteration_by_source.get(source.path, 0)
                last_step = last_web_research_steps_by_source.get(source.path, 0)
                due_by_iter = web_research_every_iterations > 0 and (iteration - last_iter) >= web_research_every_iterations
                due_by_step = web_research_every_steps > 0 and (total_steps_applied - last_step) >= web_research_every_steps
                recent_log_text = "\n".join(source_actions_log[-40:]) if source_actions_log else ""
                error_triggered = bool(WEB_RESEARCH_TRIGGER_RE.search(recent_log_text))
                research_required = error_triggered or bool(
                    re.search(r"\b(documentation|docs|spec|reference|api)\b", (source.requirements_text or ""), re.IGNORECASE)
                )
                if (due_by_iter or due_by_step) and research_required:
                    queries = _extract_web_research_queries(
                        source,
                        source_actions_log,
                        limit=max(1, web_research_max_queries),
                    )
                    if queries:
                        actions_log.append(
                            f"Running web research for {source.path} (queries={len(queries)})."
                        )
                    summaries = []
                    for query in queries:
                        results = _search_web(
                            search_engines,
                            query,
                            max_results=max(1, web_research_max_results),
                            project_root=project_root,
                            cache_ttl_hours=web_research_cache_ttl_hours,
                        )
                        documents = []
                        for result in results:
                            url = _safe_str(result.get("url"))
                            if not url:
                                continue
                            parsed = urlparse(url)
                            if parsed.scheme not in ("http", "https"):
                                continue
                            content = _fetch_web_content(
                                url,
                                timeout=web_research_fetch_timeout,
                                max_bytes=web_research_fetch_max_bytes,
                                project_root=project_root,
                                cache_ttl_hours=web_research_cache_ttl_hours,
                            )
                            documents.append(
                                {
                                    "title": _safe_str(result.get("title")),
                                    "url": url,
                                    "snippet": _safe_str(result.get("snippet")),
                                    "content": content,
                                }
                            )
                        summary = ""
                        if documents:
                            summary = _summarize_web_research(
                                researcher_provider,
                                query=query,
                                documents=documents,
                                llm_max_tokens=researcher_params.get("max_tokens"),
                                llm_temperature=researcher_params.get("temperature", llm_temperature),
                                llm_timeout=researcher_params.get("timeout"),
                                llm_reasoning_effort=researcher_params.get("reasoning_effort"),
                            )
                        if summary:
                            summaries.append(f"Query: {query}\n{summary}".strip())
                        web_research_reports.append(
                            {
                                "source": source.path,
                                "iteration": iteration,
                                "query": query,
                                "results": [
                                    {k: v for k, v in doc.items() if k != "content"} for doc in documents
                                ],
                                "summary": summary,
                            }
                        )
                    if summaries:
                        web_research_note = "\n\n".join(summaries)
                        web_research_notes_by_source[source.path] = web_research_note
                        last_web_research_iteration_by_source[source.path] = iteration
                        last_web_research_steps_by_source[source.path] = total_steps_applied
            tree_summary = _summarize_tree(
                project_root,
                extra_ignored=[solver_workspace_rel] if solver_workspace_rel and solver_workspace_within_project else None,
            )
            workspace_context_note = ""
            if workspace_preexisting:
                workspace_context_note = "Existing solver workspace content detected; reuse and extend it."
                if workspace_has_sources:
                    workspace_context_note += " TODO/FIXME items in the workspace sources are requirements for their files."
            if solver_workspace_rel:
                if output_dir_explicit and not solver_workspace_within_project:
                    workspace_note = (
                        f"Solver workspace: {solver_workspace_rel} "
                        "(explicit output dir outside project; use for venvs and generated artifacts, "
                        "but keep project code/tests in the project root unless requirements say otherwise; "
                        "use absolute paths under this directory when you do write there)."
                    )
                elif output_dir_explicit:
                    workspace_note = (
                        f"Solver workspace: {solver_workspace_rel} "
                        "(explicit output dir; use for new environments and generated artifacts unless requirements say otherwise)."
                    )
                else:
                    workspace_note = (
                        f"Solver workspace: {solver_workspace_rel} "
                        "(use for new environments and generated artifacts unless requirements specify otherwise)."
                    )
            else:
                workspace_note = "Solver workspace: none (requirements specify output location)."

            other_sources = [s.path for s in requirement_sources if s.path != source.path]
            if other_sources:
                other_sources_note = "Other requirement sources (do NOT merge in this iteration):\n- " + "\n- ".join(other_sources[:10])
                if len(other_sources) > 10:
                    other_sources_note += "\n- ..."
            else:
                other_sources_note = "Other requirement sources: none."

            source_context = source.context_excerpt or "No source-specific context available."
            source_index = source_index_map.get(source.path, 1)
            requirements_label = f"Requirement source {source_index}/{source_count}: {source.path}"
            requirements_register_section = _build_requirements_register_section(
                requirements_register, source.path
            )
            eval_section = _build_eval_data_section(eval_info)
            sample_matches = _match_sample_code_for_source(source.path, sample_code_map)
            sample_section = ""
            if sample_matches:
                sample_lines = ["Sample code available (use as a starting point if appropriate):"]
                for sample_path, sample_excerpt in sample_matches:
                    sample_lines.append(f"- {sample_path} (excerpt):\n{sample_excerpt}")
                sample_section = "\n".join(sample_lines).strip() + "\n\n"
            test_matches = _collect_related_test_snippets(
                source.path, project_root, extra_ignored=extra_ignored
            )
            test_section = ""
            if test_matches:
                test_lines = ["Related tests (expectations to satisfy):"]
                for test_path, test_excerpt in test_matches:
                    test_lines.append(f"- {test_path} (excerpt):\n{test_excerpt}")
                test_section = "\n".join(test_lines).strip() + "\n\n"
            repo_section = ""
            if repo_index:
                query_text = " ".join(
                    [
                        source.path or "",
                        source.requirements_text or "",
                        source.context_excerpt or "",
                    ]
                )
                repo_matches = repo_index.search(query_text, limit=6)
                repo_section = RepoIndex.format_matches(repo_matches)
                if repo_section:
                    repo_section = repo_section + "\n\n"
            helper_section = ""
            if helper_modules:
                helper_lines = ["Helper modules available (reuse before adding new deps):"]
                for helper_path, helper_summary in sorted(helper_modules.items()):
                    helper_lines.append(f"- {helper_path}: {helper_summary}")
                helper_section = "\n".join(helper_lines).strip() + "\n\n"
            hallucination_note = ""
            if source_hallucinations:
                unique_hallucinations = sorted(set(source_hallucinations))
                hallucination_note = (
                    "Hallucinated dependencies removed from solver workspace requirements.txt:\n- "
                    + "\n- ".join(unique_hallucinations)
                    + "\nRe-evaluate the requirement and choose valid alternatives or approaches.\n\n"
                )
            audit_section = ""
            if audit_note:
                audit_header = (
                    "Third-party audit findings (must address in this iteration):"
                    if audit_required
                    else "Third-party audit findings:"
                )
                audit_section = f"{audit_header}\n{audit_note}\n\n"
            verification_section = ""
            pending_verification = verification_failures_by_source.get(source.path)
            if pending_verification:
                verification_section = (
                    "Verification failures to address (fix before adding new work):\n"
                    f"{_summarize_failures(pending_verification, max_chars=4000)}\n\n"
                )
            research_section = ""
            if web_research_note:
                research_section = f"External research findings:\n{web_research_note}\n\n"
            progress_memory = _format_progress_memory(progress_tracker, source.path)
            user_prompt = (
                "Project tree (partial):\n"
                f"{tree_summary}\n\n"
                f"{requirements_register_section}"
                f"{eval_section}"
                f"{helper_section}"
                f"{repo_section}"
                f"{sample_section}"
                f"{test_section}"
                f"{requirements_label}:\n{source.requirements_text}\n\n"
                "Source context excerpt:\n"
                f"{source_context}\n\n"
                f"{research_section}"
                f"{audit_section}"
                f"{verification_section}"
                f"{other_sources_note}\n\n"
                f"{workspace_note}\n"
                f"{workspace_context_note}\n\n"
                f"{hallucination_note}"
                f"Iteration {iteration} of {max_iterations} for this source.\n"
                "Previous actions log (most recent):\n"
                f"{_trim_actions_log(source_actions_log) if source_actions_log else 'None yet.'}\n\n"
                f"{progress_memory}"
                "Respond with JSON only. Schema:\n"
                "{\n"
                '  "summary": "short overview of intent",\n'
                '  "requirements": ["REQ-001", "REQ-002"],\n'
                '  "done": false,\n'
                '  "plan": [\n'
                "    {\n"
                '      "type": "note|create_dir|write_file|append_file|replace_in_file|run_command",\n'
                '      "step": "human readable step",\n'
                '      "path": "relative path for file operations (project root) or absolute path under solver workspace if outside project root",\n'
                '      "content": "file content for write/append",\n'
                '      "overwrite": true,\n'
                '      "find": "text to replace",\n'
                '      "replace": "replacement text",\n'
                '      "count": 0,\n'
                '      "command": "shell command",\n'
                '      "workdir": "relative dir (project root) or absolute dir under solver workspace if outside project root",\n'
                '      "timeout": 600\n'
                "    }\n"
                "  ],\n"
                '  "verification_steps": [\n'
                "    {\n"
                '      "type": "run_command",\n'
                '      "step": "verification step",\n'
                '      "command": "shell command",\n'
                '      "workdir": "relative dir (project root) or absolute dir under solver workspace if outside project root",\n'
                '      "timeout": 600\n'
                "    }\n"
                "  ]\n"
                "}\n"
        "Notes:\n"
        "- Treat ONLY the current requirement source. Do not merge requirements from other files unless explicitly requested.\n"
        "- Reference requirement IDs (REQ-###) from the formal register in every plan step and in any code comments you add.\n"
        "- Ensure at least one global requirement ID is referenced in the plan steps.\n"
        "- Prefer write_file for new files, replace_in_file for edits, and run_command only when necessary.\n"
        "- Keep changes within the project root, or within the solver workspace if provided outside the project root. Do not delete files.\n"
        "- Ignore virtual environments and third-party packages; do not propose edits inside site-packages or venv folders.\n"
        "- If you write or modify code, ensure it is robust, secure, resilient, modular, scalable, and follows best practices. "
        "Include brief inline documentation for non-obvious logic.\n"
        "- If audit findings are provided, include at least one plan step prefixed with 'AUDIT:' that explicitly addresses them.\n"
        "- Use exact filenames from requirements and the project tree; do not invent similarly named variants (pluralized/renumbered). If a target file exists, edit it rather than creating a new one.\n"
        "- Avoid placeholder implementations (constant defaults, empty returns). Implement a deterministic baseline and layer optional improvements as needed.\n"
        "- When accuracy/quality targets are mentioned, implement a baseline and an optional advanced mode (feature flag/CLI) plus caching or staged heuristics before LLM calls.\n"
        "- Normalise outputs to canonical labels/units expected by eval data/tests; add parsing and validation.\n"
        "- If eval_data schema is shown, treat records as dicts with those keys; do not assume dataclass/attribute access (e.g., use data['filepath'] not data.filepath).\n"
        "- When eval_data includes list-valued keys (e.g., latlong), treat them as positional lists (index 0 = lat, index 1 = lon).\n"
        "- If PDFDocument (or similar helper) exposes a load() constructor, use it instead of calling the BaseModel constructor with positional args.\n"
        "- If the task text hints at starter code (e.g., 'colleague has written some code to get started'), build on that file rather than replacing it.\n"
        "- Prefer shared utilities across related files to reduce duplication and keep behaviour consistent.\n"
        "- For solution files, include a module-level docstring that summarises the formal requirements and the workflow implemented.\n"
        "- Ensure generated Python files are syntactically valid and do not contain duplicate/conflicting implementations.\n"
        "- When code is created or changed, include pytest (or equivalent) coverage and a run_command step to execute tests "
        "or run the code to validate outputs. If results are not as expected, add follow-up steps to address gaps.\n"
        "- If your code relies on environment variables, add explicit checks (prompt or clear error) for missing values and "
        "include tests/verification for the missing-value path.\n"
        "- If verification output shows a FutureWarning, treat it as a failure and apply the suggested fix before retrying.\n"
        "- If you need to run commands in the solver workspace, set workdir to the absolute workspace path explicitly.\n"
        "- If you create or modify Python code or install Python dependencies, include steps to create a venv in the solver workspace (or specified output location) and install dependencies there unless requirements say otherwise (e.g., python -m venv <workspace>/venv).\n"
        "- When installing Python packages, use <venv>/bin/python -m pip or <venv>/bin/pip to ensure the venv is used; do not rely on activation across steps.\n"
        "- Do not chain shell commands with quoted '&&'. Prefer separate run_command steps; if you must chain, use plain && without quotes.\n"
        "- When creating a venv, use system python (python3 -m venv <path>) rather than a python executable inside the target venv.\n"
        "- Do not invent package names; if a dependency is unclear, call it out and prefer standard-library alternatives.\n"
        "- Do not create or overwrite venv activation scripts directly; use python -m venv to create them.\n"
        "- If eval_data.json or load_eval_data() is present, use it for evaluation and do not hardcode single-file runs.\n"
        "- If all requirements for this source are satisfied, set done=true and plan=[]\n"
    )

            payload = None
            codingagent_used = None
            pure_code_request = _source_is_pure_code_request(source)
            if (
                codingagent_primary_mode == "primary"
                and pure_code_request
                and codingagent_primary
                and codingagent_primary != "llm"
            ):
                coding_prompt = user_prompt
                if pure_code_request:
                    coding_prompt += (
                        "\n\nPure code mode:\n"
                        "- Return JSON with only write_file/append_file/replace_in_file steps.\n"
                        "- Do not include run_command or notes.\n"
                        "- Include test file changes where appropriate.\n"
                        "- Keep output minimal and code-focused.\n"
                    )
                workspace_for_agent = solver_workspace or project_root
                payload = _query_codingagent_plan(
                    agent=codingagent_primary,
                    prompt=coding_prompt,
                    provider=planner_provider,
                    system_prompt=system_prompt,
                    llm_max_tokens=planner_params.get("max_tokens"),
                    llm_temperature=planner_params.get("temperature", llm_temperature),
                    llm_timeout=planner_params.get("timeout"),
                    llm_reasoning_effort=planner_params.get("reasoning_effort"),
                    actions_log=actions_log,
                    workspace=workspace_for_agent,
                    output_path=output_path,
                    allow_run=allow_run,
                    llm_provider=llm_provider,
                    llm_api_key=llm_api_key,
                    codingagent_model=codingagent_model,
                    codingagent_reasoning_effort=codingagent_reasoning_effort,
                )
                codingagent_used = codingagent_primary if payload else None
                if not payload and codingagent_fallback_norm and codingagent_fallback_norm not in {"llm", codingagent_primary}:
                    payload = _query_codingagent_plan(
                        agent=codingagent_fallback_norm,
                        prompt=coding_prompt,
                        provider=planner_provider,
                        system_prompt=system_prompt,
                        llm_max_tokens=planner_params.get("max_tokens"),
                        llm_temperature=planner_params.get("temperature", llm_temperature),
                        llm_timeout=planner_params.get("timeout"),
                        llm_reasoning_effort=planner_params.get("reasoning_effort"),
                        actions_log=actions_log,
                        workspace=workspace_for_agent,
                        output_path=output_path,
                        allow_run=allow_run,
                        llm_provider=llm_provider,
                        llm_api_key=llm_api_key,
                        codingagent_model=codingagent_model,
                        codingagent_reasoning_effort=codingagent_reasoning_effort,
                    )
                    codingagent_used = codingagent_fallback_norm if payload else None
                if payload and codingagent_used:
                    payload = dict(payload)
                    payload["provider"] = payload.get("provider") or codingagent_used
                    payload["codingagent"] = codingagent_used
                    if pure_code_request:
                        payload["codingagent_code_only"] = True
                    codingagent_used_sources.setdefault(codingagent_used, set()).add(source.path)
                    if codingagent_used == "opencode":
                        opencode_used_sources.add(source.path)
                    _record_action(f"Using coding agent plan ({codingagent_used}).", source_actions_log)

            if payload is None:
                resp = planner_provider.predict(
                    [{"role": "user", "content": user_prompt}],
                    system=system_prompt,
                    max_tokens=planner_params.get("max_tokens"),
                    temperature=planner_params.get("temperature", llm_temperature),
                    timeout=planner_params.get("timeout"),
                    reasoning_effort=planner_params.get("reasoning_effort"),
                )

            def _maybe_codingagent_fallback(reason: str, *, requires_code: bool) -> Optional[Dict[str, object]]:
                if not requires_code:
                    return None
                count = inadequate_counts.get(source.path, 0) + 1
                inadequate_counts[source.path] = count
                _record_action(f"Inadequate LLM plan: {reason} (count {count}).", source_actions_log)
                if count < _opencode_threshold():
                    return None
                agents_to_try = []
                if codingagent_primary and codingagent_primary != "llm":
                    agents_to_try.append(codingagent_primary)
                if codingagent_fallback_norm and codingagent_fallback_norm != "llm" and codingagent_fallback_norm not in agents_to_try:
                    agents_to_try.append(codingagent_fallback_norm)
                if not agents_to_try:
                    return None
                pure_code = _source_is_pure_code_request(source)
                coding_prompt = user_prompt
                if pure_code:
                    coding_prompt += (
                        "\n\nPure code mode:\n"
                        "- Return JSON with only write_file/append_file/replace_in_file steps.\n"
                        "- Do not include run_command or notes.\n"
                        "- Include test file changes where appropriate.\n"
                        "- Keep output minimal and code-focused.\n"
                    )
                workspace_for_agent = solver_workspace or project_root
                for agent in agents_to_try:
                    agent_payload = _query_codingagent_plan(
                        agent=agent,
                        prompt=coding_prompt,
                        provider=planner_provider,
                        system_prompt=system_prompt,
                        llm_max_tokens=planner_params.get("max_tokens"),
                        llm_temperature=planner_params.get("temperature", llm_temperature),
                        llm_timeout=planner_params.get("timeout"),
                        llm_reasoning_effort=planner_params.get("reasoning_effort"),
                        actions_log=actions_log,
                        workspace=workspace_for_agent,
                        output_path=output_path,
                        allow_run=allow_run,
                        llm_provider=llm_provider,
                        llm_api_key=llm_api_key,
                        codingagent_model=codingagent_model,
                        codingagent_reasoning_effort=codingagent_reasoning_effort,
                    )
                    if not agent_payload:
                        continue
                    agent_payload = dict(agent_payload)
                    agent_payload["source_path"] = source.path
                    agent_payload["iteration"] = iteration
                    agent_payload["provider"] = agent_payload.get("provider") or agent
                    agent_payload["codingagent"] = agent
                    if pure_code:
                        agent_payload["codingagent_code_only"] = True
                    plan_steps = agent_payload.get("plan")
                    if pure_code and isinstance(plan_steps, list):
                        if _plan_has_code_changes(plan_steps) and not _plan_has_verification(plan_steps):
                            plan_steps.append(
                                {
                                    "type": "run_command",
                                    "step": "Run tests (auto-added after code-only coding agent output)",
                                    "command": "python -m pytest",
                                    "workdir": ".",
                                    "timeout": 900,
                                }
                            )
                            agent_payload["plan"] = plan_steps
                            _record_action(
                                "Added verification command after code-only coding agent output.",
                                source_actions_log,
                            )
                    all_plans.append(agent_payload)
                    codingagent_used_sources.setdefault(agent, set()).add(source.path)
                    if agent == "opencode":
                        opencode_used_sources.add(source.path)
                    _record_action(f"Using coding agent fallback plan ({agent}).", source_actions_log)
                    return agent_payload
                return None

            if payload is None:
                schema_hint = _extract_schema_from_prompt(user_prompt)
                try:
                    payload = _parse_json_payload(resp.text or "{}")
                except json.JSONDecodeError:
                    payload = _rephrase_json_payload(
                        planner_provider,
                        response_text=resp.text or "",
                        schema_hint=schema_hint,
                        llm_max_tokens=planner_params.get("max_tokens"),
                        llm_temperature=planner_params.get("temperature", llm_temperature),
                        llm_timeout=planner_params.get("timeout"),
                        llm_reasoning_effort=planner_params.get("reasoning_effort"),
                        actions_log=actions_log,
                        label=f"plan:{source.path}",
                    )
                    if payload is None:
                        payload = _maybe_codingagent_fallback(
                            "invalid JSON response",
                            requires_code=source_requires_code,
                        )
                        if not payload:
                            continue
            raw_payload = payload
            payload = _coerce_plan_payload(payload)
            if payload is None:
                payload = _maybe_codingagent_fallback(
                    "invalid plan payload",
                    requires_code=source_requires_code,
                )
                if not payload:
                    continue
                raw_payload = payload
                payload = _coerce_plan_payload(payload)
                if payload is None:
                    continue
            if isinstance(raw_payload, list):
                _record_action("Coerced list payload into plan steps.", source_actions_log)
            elif isinstance(raw_payload, dict) and "plan" not in raw_payload:
                for key in ("steps", "actions"):
                    if isinstance(raw_payload.get(key), list):
                        _record_action(f"Mapped '{key}' to plan steps.", source_actions_log)
                        break
            plan_steps = payload.get("plan", [])
            if not isinstance(plan_steps, list):
                payload = _maybe_codingagent_fallback("missing plan list", requires_code=source_requires_code)
                if not payload:
                    continue
                raw_payload = payload
                payload = _coerce_plan_payload(payload)
                if payload is None:
                    continue
                if isinstance(raw_payload, list):
                    _record_action("Coerced list payload into plan steps.", source_actions_log)
                elif isinstance(raw_payload, dict) and "plan" not in raw_payload:
                    for key in ("steps", "actions"):
                        if isinstance(raw_payload.get(key), list):
                            _record_action(f"Mapped '{key}' to plan steps.", source_actions_log)
                            break
                plan_steps = payload.get("plan", [])
                if not isinstance(plan_steps, list):
                    continue

            verification_steps = payload.get("verification_steps")
            if isinstance(verification_steps, list) and verification_steps:
                added = 0
                for step in verification_steps:
                    if not isinstance(step, dict):
                        continue
                    step_type = _normalize_step_type(step.get("type"))
                    if step_type and step_type != "run_command":
                        continue
                    plan_steps.append(step)
                    added += 1
                if added:
                    _record_action(
                        f"Appended {added} verification step(s) from payload.",
                        source_actions_log,
                    )

            plan_entry_id = None
            parent_path_id = progress_last_id_by_source.get(source.path)
            plan_entry_id = progress_tracker.record(
                source=source.path,
                iteration=iteration,
                status="planned",
                notes=_safe_str(payload.get("summary")),
                data={
                    "requirements": payload.get("requirements"),
                    "steps": len(plan_steps) if isinstance(plan_steps, list) else 0,
                    "provider": payload.get("provider") or llm_provider,
                },
                parent_id=parent_path_id,
            )
            progress_last_id_by_source[source.path] = plan_entry_id

            payload_with_meta = dict(payload)
            payload_with_meta["source_path"] = source.path
            payload_with_meta["iteration"] = iteration
            if "provider" not in payload_with_meta:
                payload_with_meta["provider"] = llm_provider
            all_plans.append(payload_with_meta)
            cycle.record(
                "plan",
                PhaseResult.ok(
                    data={
                        "steps": len(plan_steps) if isinstance(plan_steps, list) else 0,
                        "done": bool(payload.get("done")),
                        "provider": payload_with_meta.get("provider"),
                    }
                ),
            )

            if payload.get("done") is True or not plan_steps:
                if source_requires_code:
                    opencode_payload = _maybe_codingagent_fallback(
                        "done/no plan for code-required source",
                        requires_code=True,
                    )
                    if opencode_payload:
                        payload = opencode_payload
                        plan_steps = payload.get("plan", [])
                        if not isinstance(plan_steps, list):
                            continue
                    else:
                        continue
                else:
                    _record_action(
                        f"Solver marked done or no plan steps returned for source: {source.path}",
                        source_actions_log,
                    )
                    cycle.record(
                        "act",
                        PhaseResult.ok(
                            notes="no plan steps",
                            data={"steps_applied": 0},
                        ),
                    )
                    cycle.record(
                        "verify",
                        PhaseResult.ok(
                            notes="no verification required",
                            data={"unresolved_failures": 0},
                        ),
                    )
                    cycle.record(
                        "reflect",
                        PhaseResult.halt("done or no plan steps", data={"scope": "source"}),
                    )
                    parent_path_id = progress_last_id_by_source.get(source.path)
                    done_entry_id = progress_tracker.record(
                        source=source.path,
                        iteration=iteration,
                        status="done",
                        notes="done or no plan steps",
                        data={"provider": payload.get("provider") or llm_provider},
                        parent_id=parent_path_id,
                    )
                    progress_last_id_by_source[source.path] = done_entry_id
                    progress_checkpoint_by_source[source.path] = done_entry_id
                    break

            plan_has_code_changes = _plan_has_code_changes(plan_steps)
            strict_requirement_refs = _env_bool("SOLVER_STRICT_REQUIREMENT_REFS", False)
            strict_verification = _env_bool("SOLVER_STRICT_VERIFICATION", False)
            strict_test_coverage = _env_bool("SOLVER_STRICT_TEST_COVERAGE", False)
            verification_first = _env_bool("SOLVER_VERIFICATION_FIRST", True)
            if requirements_register_ids:
                _ensure_plan_requirement_refs(
                    plan_steps,
                    payload_requirements=payload.get("requirements"),
                    required_ids=source_required_ids,
                    global_ids=requirements_register_global_ids,
                    sequence_ids=requirements_register_sequence_ids,
                    all_ids=requirements_register_ids,
                    strict=strict_requirement_refs,
                )
                if strict_requirement_refs:
                    missing_ref_steps = _plan_steps_missing_requirement_refs(plan_steps)
                    referenced_ids = _extract_requirement_refs_from_plan(
                        plan_steps, payload.get("requirements")
                    )
                    missing_required_ids = sorted(source_required_ids - referenced_ids) if source_required_ids else []
                    missing_global_refs = bool(
                        requirements_register_global_ids
                        and not (referenced_ids & requirements_register_global_ids)
                    )
                    missing_sequence_refs = bool(
                        requirements_register_sequence_ids
                        and not (referenced_ids & requirements_register_sequence_ids)
                    )
                    if missing_ref_steps or missing_global_refs or missing_sequence_refs or missing_required_ids:
                        reason = "missing requirement ID references"
                        if missing_global_refs:
                            reason = "missing global requirement ID references"
                        if missing_sequence_refs:
                            reason = "missing sequence requirement ID references"
                        if missing_required_ids:
                            missing_preview = ", ".join(missing_required_ids[:6])
                            reason = f"missing required IDs for source ({missing_preview})"
                        opencode_payload = _maybe_codingagent_fallback(
                            reason,
                            requires_code=source_requires_code,
                        )
                        if opencode_payload:
                            payload = opencode_payload
                            plan_steps = payload.get("plan", [])
                            if not isinstance(plan_steps, list):
                                continue
                            plan_has_code_changes = _plan_has_code_changes(plan_steps)
                        else:
                            if missing_ref_steps:
                                _record_action(
                                    f"Plan steps missing requirement IDs at indices: {missing_ref_steps}; retrying.",
                                    source_actions_log,
                                )
                            if missing_global_refs:
                                _record_action(
                                    "Plan missing global requirement ID references; retrying.",
                                    source_actions_log,
                                )
                            if missing_sequence_refs:
                                _record_action(
                                    "Plan missing sequence requirement ID references; retrying.",
                                    source_actions_log,
                                )
                            if missing_required_ids:
                                _record_action(
                                    "Plan missing required IDs for this source: "
                                    + ", ".join(missing_required_ids[:12])
                                    + (" ...(truncated)" if len(missing_required_ids) > 12 else ""),
                                    source_actions_log,
                                )
                            continue
            if audit_required and audit_note:
                _ensure_audit_step(plan_steps, audit_note)
                if not _plan_has_audit_marker(plan_steps):
                    reason = "missing audit realignment steps"
                    opencode_payload = _maybe_codingagent_fallback(
                        reason,
                        requires_code=source_requires_code,
                    )
                    if opencode_payload:
                        payload = opencode_payload
                        plan_steps = payload.get("plan", [])
                        if not isinstance(plan_steps, list):
                            continue
                    else:
                        _record_action(
                            "Plan missing AUDIT-prefixed steps to address audit findings; retrying.",
                            source_actions_log,
                        )
                        continue
            if plan_has_code_changes and not _plan_has_verification(plan_steps):
                if not strict_verification and allow_run:
                    plan_steps.append(
                        {
                            "type": "run_command",
                            "step": "Run tests to verify changes",
                            "command": "python -m pytest",
                            "workdir": ".",
                            "timeout": 900,
                        }
                    )
                    _ensure_plan_requirement_refs(
                        plan_steps,
                        payload_requirements=payload.get("requirements"),
                        required_ids=source_required_ids,
                        global_ids=requirements_register_global_ids,
                        sequence_ids=requirements_register_sequence_ids,
                        all_ids=requirements_register_ids,
                        strict=False,
                    )
                else:
                    opencode_payload = _maybe_codingagent_fallback(
                        "code changes without verification",
                        requires_code=True,
                    )
                    if opencode_payload:
                        payload = opencode_payload
                        plan_steps = payload.get("plan", [])
                        if not isinstance(plan_steps, list):
                            continue
                    else:
                        continue
                plan_has_code_changes = _plan_has_code_changes(plan_steps)
            if plan_has_code_changes and not _plan_has_test_changes(plan_steps):
                if strict_test_coverage:
                    opencode_payload = _maybe_codingagent_fallback(
                        "code changes without test coverage additions",
                        requires_code=True,
                    )
                    if opencode_payload:
                        payload = opencode_payload
                        plan_steps = payload.get("plan", [])
                        if not isinstance(plan_steps, list):
                            continue
                    else:
                        continue

            if plan_has_code_changes:
                syntax_issues = _plan_python_syntax_issues(plan_steps)
                if syntax_issues:
                    reason = "; ".join(syntax_issues)
                    opencode_payload = _maybe_codingagent_fallback(
                        f"invalid python syntax in plan: {reason}",
                        requires_code=True,
                    )
                    if opencode_payload:
                        payload = opencode_payload
                        plan_steps = payload.get("plan", [])
                        if not isinstance(plan_steps, list):
                            continue
                    else:
                        _record_action(
                            f"Plan rejected due to Python syntax errors ({reason}); retrying.",
                            source_actions_log,
                        )
                        continue
            if source_requires_code and not _plan_has_actionable_steps(plan_steps):
                opencode_payload = _maybe_codingagent_fallback(
                    "no actionable steps in plan",
                    requires_code=True,
                )
                if opencode_payload:
                    payload = opencode_payload
                    plan_steps = payload.get("plan", [])
                    if not isinstance(plan_steps, list):
                        continue
                else:
                    _record_action("Plan contained only notes; retrying.", source_actions_log)
                    continue
                _normalize_plan_step_paths(plan_steps)
                suspicious_issues = _plan_suspicious_issues(plan_steps, eval_info)
                if suspicious_issues:
                    reason = "; ".join(suspicious_issues)
                    opencode_payload = _maybe_codingagent_fallback(
                        f"suspicious plan patterns: {reason}",
                        requires_code=True,
                    )
                    if opencode_payload:
                        payload = opencode_payload
                        plan_steps = payload.get("plan", [])
                        if not isinstance(plan_steps, list):
                            continue
                    else:
                        _record_action(
                            f"Plan flagged for likely schema or helper misuse ({reason}); retrying.",
                            source_actions_log,
                        )
                        continue

            remaining_steps = max_steps - total_steps_applied
            if remaining_steps <= 0:
                _record_action("Max steps reached; stopping iterations.", source_actions_log)
                break

            iteration_steps_applied = 0
            iteration_unresolved_start = len(unresolved_failures)
            replan_due_to_verification = False
            verification_steps_executed = 0
            current_venv = _find_workspace_venv(solver_workspace)
            replan_due_to_hallucination = False
            execution_steps = plan_steps
            if verification_first and allow_run:
                non_ver_steps, ver_steps = _split_verification_steps(plan_steps)
                if ver_steps:
                    available_non_ver = max(0, remaining_steps - len(ver_steps))
                    if available_non_ver < len(non_ver_steps):
                        _record_action(
                            "Truncated non-verification steps to reserve verification budget.",
                            source_actions_log,
                        )
                    execution_steps = non_ver_steps[:available_non_ver] + ver_steps
            for idx, step in enumerate(execution_steps[:remaining_steps]):
                if not isinstance(step, dict):
                    _record_action(f"Skipped non-dict step at index {idx}.", source_actions_log)
                    continue
                before_len = len(actions_log)
                before_hallucinations = len(source_hallucinations)
                command_failures: List[Dict[str, object]] = []
                is_verification_step = False
                if _normalize_step_type(step.get("type")) == "run_command":
                    command = step.get("command")
                    if isinstance(command, str) and _is_verification_command(command):
                        is_verification_step = True
                _apply_step(
                    project_root,
                    step,
                    allow_run=allow_run,
                    actions_log=actions_log,
                    allowed_roots=allowed_roots,
                    failure_log=command_failures,
                    venv_path=current_venv,
                    workspace_root=solver_workspace,
                    hallucination_log=source_hallucinations,
                    prefer_workspace_new_files=prefer_workspace_new_files,
                    applied_steps=source_applied_steps,
                    dataset_summary=dataset_summary,
                    eval_info=eval_info,
                )
                if len(actions_log) > before_len:
                    source_actions_log.extend(actions_log[before_len:])
                total_steps_applied += 1
                iteration_steps_applied += 1
                if is_verification_step:
                    verification_steps_executed += 1
                if solver_workspace:
                    current_venv = _find_workspace_venv(solver_workspace)
                if len(source_hallucinations) > before_hallucinations:
                    new_items = source_hallucinations[before_hallucinations:]
                    _record_action(
                        "Removed hallucinated requirements: " + ", ".join(new_items),
                        source_actions_log,
                    )
                    _record_action(
                        "Replanning current source due to hallucinated requirements.",
                        source_actions_log,
                    )
                    replan_due_to_hallucination = True
                    break

                if command_failures and allow_run:
                    venv_path = current_venv or _find_workspace_venv(solver_workspace)
                    recovery_attempts = 0
                    failures_to_fix = command_failures
                    while failures_to_fix and recovery_attempts < 2:
                        recovery_attempts += 1
                        _record_action(
                            f"Attempting recovery for {len(failures_to_fix)} failed command(s) (attempt {recovery_attempts}).",
                            source_actions_log,
                        )
                        recovery_steps = _build_indentation_recovery_steps(
                            failures_to_fix,
                            project_root=project_root,
                            actions_log=source_actions_log,
                        )
                        if not recovery_steps:
                            recovery_steps = _plan_recovery_steps(
                                reviewer_provider,
                                failures_to_fix,
                                workspace_note=workspace_note,
                                venv_path=venv_path,
                                actions_log=source_actions_log,
                                llm_max_tokens=reviewer_params.get("max_tokens"),
                                llm_temperature=reviewer_params.get("temperature", llm_temperature),
                                llm_timeout=reviewer_params.get("timeout"),
                                llm_reasoning_effort=reviewer_params.get("reasoning_effort"),
                            )
                        if not recovery_steps:
                            _record_action("Recovery planning returned no steps; skipping.", source_actions_log)
                            break
                        failures_to_fix = []
                        for rec_idx, rec_step in enumerate(recovery_steps):
                            before_rec_len = len(actions_log)
                            before_rec_hallucinations = len(source_hallucinations)
                            if total_steps_applied >= max_steps:
                                _record_action("Max steps reached; skipping remaining recovery steps.", source_actions_log)
                                break
                            rec_is_verification = False
                            if _normalize_step_type(rec_step.get("type")) == "run_command":
                                rec_command = rec_step.get("command")
                                if isinstance(rec_command, str) and _is_verification_command(rec_command):
                                    rec_is_verification = True
                            _apply_step(
                                project_root,
                                rec_step,
                                allow_run=allow_run,
                                actions_log=actions_log,
                                allowed_roots=allowed_roots,
                                failure_log=failures_to_fix,
                                venv_path=venv_path,
                                workspace_root=solver_workspace,
                                hallucination_log=source_hallucinations,
                                prefer_workspace_new_files=prefer_workspace_new_files,
                                applied_steps=source_applied_steps,
                                dataset_summary=dataset_summary,
                                eval_info=eval_info,
                            )
                            if len(actions_log) > before_rec_len:
                                source_actions_log.extend(actions_log[before_rec_len:])
                            total_steps_applied += 1
                            iteration_steps_applied += 1
                            if rec_is_verification:
                                verification_steps_executed += 1
                            if solver_workspace:
                                current_venv = _find_workspace_venv(solver_workspace)
                            if len(source_hallucinations) > before_rec_hallucinations:
                                new_items = source_hallucinations[before_rec_hallucinations:]
                                _record_action(
                                    "Removed hallucinated requirements: " + ", ".join(new_items),
                                    source_actions_log,
                                )
                                _record_action(
                                    "Replanning current source due to hallucinated requirements.",
                                    source_actions_log,
                                )
                                replan_due_to_hallucination = True
                                break
                        if replan_due_to_hallucination:
                            failures_to_fix = []
                            break
                        if failures_to_fix:
                            _record_action(
                                f"Recovery attempt {recovery_attempts} still has failed commands.",
                                source_actions_log,
                            )
                        if total_steps_applied >= max_steps:
                            _record_action("Max steps reached during recovery; stopping iterations.", source_actions_log)
                            break

                    if failures_to_fix:
                        verification_failures = [
                            failure for failure in failures_to_fix if _is_verification_failure(failure)
                        ]
                        if verification_failures and verification_first and allow_run:
                            verification_failures_by_source[source.path] = verification_failures
                            replan_due_to_verification = True
                            _record_action(
                                "Verification failed; replanning current source before proceeding.",
                                source_actions_log,
                            )
                        else:
                            for failure in failures_to_fix:
                                entry = dict(failure)
                                entry.update(
                                    {
                                        "source": source.path,
                                        "iteration": iteration,
                                        "recovery_attempts": recovery_attempts,
                                        "status": "unresolved",
                                    }
                                )
                                unresolved_failures.append(entry)
                            _record_action(
                                f"Recorded {len(failures_to_fix)} unresolved failure(s) for TODO tracking.",
                                source_actions_log,
                            )
                            if source_queue and source.path not in deferred_once:
                                deferred_once.add(source.path)
                                defer_source = True
                                _record_action(
                                    "Deferring current requirement source to proceed with other sources; will retry later.",
                                    source_actions_log,
                                )
                    if replan_due_to_verification:
                        break
                    if defer_source:
                        break

            iteration_new_unresolved = max(0, len(unresolved_failures) - iteration_unresolved_start)
            cycle.record(
                "act",
                PhaseResult.ok(
                    data={
                        "steps_applied": iteration_steps_applied,
                        "replan_due_to_hallucination": replan_due_to_hallucination,
                        "replan_due_to_verification": replan_due_to_verification,
                        "defer_source": defer_source,
                    }
                ),
            )
            cycle.record(
                "verify",
                PhaseResult.ok(
                    data={
                        "new_unresolved_failures": iteration_new_unresolved,
                        "total_unresolved_failures": len(unresolved_failures),
                        "verification_steps_executed": verification_steps_executed,
                    }
                ),
            )
            if plan_entry_id:
                if replan_due_to_hallucination:
                    retrace_to = progress_checkpoint_by_source.get(source.path)
                    progress_tracker.mark_dead_end(
                        plan_entry_id,
                        "replan due to hallucination",
                        retrace_to=retrace_to,
                    )
                    if retrace_to:
                        _record_action(
                            f"Retrace to checkpoint {retrace_to} after hallucination dead-end.",
                            source_actions_log,
                        )
                elif replan_due_to_verification:
                    retrace_to = progress_checkpoint_by_source.get(source.path)
                    progress_tracker.mark_dead_end(
                        plan_entry_id,
                        "replan due to verification failure",
                        retrace_to=retrace_to,
                    )
                    if retrace_to:
                        _record_action(
                            f"Retrace to checkpoint {retrace_to} after verification dead-end.",
                            source_actions_log,
                        )
                elif defer_source:
                    progress_tracker.update(
                        plan_entry_id,
                        status="deferred",
                        notes="deferred after unresolved failures",
                    )
                else:
                    progress_tracker.update(
                        plan_entry_id,
                        status="verified" if verification_steps_executed else "progressed",
                    )
                    progress_checkpoint_by_source[source.path] = plan_entry_id
            if verification_steps_executed and not replan_due_to_verification:
                verification_failures_by_source.pop(source.path, None)
            if replan_due_to_hallucination:
                cycle.record(
                    "reflect",
                    PhaseResult.retry("replan due to hallucination", data={"scope": "source"}),
                )
                continue
            if replan_due_to_verification:
                cycle.record(
                    "reflect",
                    PhaseResult.retry("replan due to verification failure", data={"scope": "source"}),
                )
                continue
            if defer_source:
                cycle.record(
                    "reflect",
                    PhaseResult.halt("deferred source", data={"scope": "source"}),
                )
                break
            if total_steps_applied >= max_steps:
                cycle.record(
                    "reflect",
                    PhaseResult.halt("max steps reached", data={"scope": "run"}),
                )
                _record_action("Max steps reached; stopping iterations.", source_actions_log)
                break
            cycle.record("reflect", PhaseResult.ok("continue"))
        pending_verification = verification_failures_by_source.get(source.path)
        if pending_verification and (defer_source or total_steps_applied >= max_steps or last_iteration >= max_iterations):
            for failure in pending_verification:
                entry = dict(failure)
                entry.update(
                    {
                        "source": source.path,
                        "iteration": last_iteration,
                        "recovery_attempts": 0,
                        "status": "unresolved",
                        "verification_issue": failure.get("verification_issue"),
                    }
                )
                unresolved_failures.append(entry)
            _record_action(
                f"Recorded {len(pending_verification)} unresolved verification failure(s) for TODO tracking.",
                source_actions_log,
            )
            verification_failures_by_source.pop(source.path, None)
        if defer_source:
            source_queue.append(source)
            actions_log.append(f"Deferred requirement source: {source.path}")
            continue
        actions_log.append(f"Completed requirement source: {source.path}")
        if total_steps_applied >= max_steps:
            break

    final_plan_state = _extract_plan_state(all_plans)
    source_paths = [src.path for src in requirement_sources]
    completed_sources_final = sorted(
        {
            path
            for path, entry in final_plan_state.items()
            if isinstance(entry, dict) and entry.get("done") is True
        }
    )
    sources_with_plans = set(final_plan_state.keys())
    unstarted_sources = [path for path in source_paths if path not in sources_with_plans]
    incomplete_sources = [path for path in source_paths if path not in completed_sources_final]
    final_source_logs = _extract_source_logs(actions_log)
    requirement_coverage, coverage_missing_sources = _build_requirement_coverage(
        requirement_sources,
        applied_steps_by_source,
        project_root,
        source_logs=final_source_logs,
        requirements_register=requirements_register,
    )
    requirement_traceability = _build_requirement_traceability(
        requirements_register,
        all_plans,
        applied_steps_by_source,
        project_root,
    )
    refs_in_plans = _collect_requirement_refs_from_plans(all_plans)
    requirements_missing_ids: List[str] = []
    requirements_referenced = 0
    if isinstance(requirements_register_list, list):
        for req in requirements_register_list:
            if not isinstance(req, dict):
                continue
            req_id = _safe_str(req.get("id")).upper()
            if not req_id:
                continue
            if req_id in refs_in_plans:
                requirements_referenced += 1
            else:
                requirements_missing_ids.append(req_id)
    requirements_sanity = {
        "total": len(requirements_register_list) if isinstance(requirements_register_list, list) else 0,
        "referenced": requirements_referenced,
        "missing_ids": requirements_missing_ids,
        "status": "ok" if not requirements_missing_ids else "missing",
    }
    if requirements_missing_ids:
        actions_log.append(
            "Requirements sanity check missing IDs: "
            + ", ".join(requirements_missing_ids[:20])
            + (" ...(truncated)" if len(requirements_missing_ids) > 20 else "")
        )
    if coverage_missing_sources:
        actions_log.append(
            "Requirement coverage missing for sources: " + ", ".join(sorted(coverage_missing_sources))
        )
        incomplete_sources = sorted(set(incomplete_sources) | set(coverage_missing_sources))
        completed_sources_final = sorted(
            [path for path in completed_sources_final if path not in coverage_missing_sources]
        )
    unresolved_verification_failures = [
        failure for failure in unresolved_failures if _is_verification_failure(failure)
    ]
    iterations_exhausted_sources = []
    for path in incomplete_sources:
        entry = final_plan_state.get(path, {})
        last_iter = entry.get("last_iteration") if isinstance(entry, dict) else 0
        if isinstance(last_iter, int) and last_iter >= max_iterations:
            iterations_exhausted_sources.append(path)
    max_steps_reached = total_steps_applied >= max_steps
    needs_more_iterations = bool(
        incomplete_sources
        and (
            iterations_exhausted_sources
            or unstarted_sources
            or max_steps_reached
            or coverage_missing_sources
        )
    )
    if requirements_missing_ids:
        needs_more_iterations = True
    if unresolved_verification_failures:
        needs_more_iterations = True
    completion_summary = {
        "total_sources": len(source_paths),
        "completed_sources": completed_sources_final,
        "incomplete_sources": incomplete_sources,
        "unstarted_sources": unstarted_sources,
        "iterations_exhausted_sources": iterations_exhausted_sources,
        "coverage_missing_sources": sorted(coverage_missing_sources),
        "max_steps_reached": max_steps_reached,
        "steps_applied": total_steps_applied,
        "max_steps": max_steps,
        "max_iterations": max_iterations,
        "needs_more_iterations": needs_more_iterations,
        "requirements_missing_ids": requirements_missing_ids,
        "unresolved_verification_failures": [
            {
                "command": failure.get("command"),
                "workdir": failure.get("workdir"),
                "exit_code": failure.get("exit_code"),
                "verification_issue": failure.get("verification_issue"),
                "source": failure.get("source"),
            }
            for failure in unresolved_verification_failures
        ],
    }
    actions_log.append(
        "Completion summary: "
        f"{len(completed_sources_final)}/{len(source_paths)} sources done; "
        f"{len(incomplete_sources)} incomplete; "
        f"steps {total_steps_applied}/{max_steps}; "
        f"needs_more_iterations={needs_more_iterations}"
    )

    todo_items: List[Dict[str, object]] = []
    for failure in unresolved_failures:
        todo_items.append(
            {
                "type": "unresolved_failure",
                "source": failure.get("source"),
                "iteration": failure.get("iteration"),
                "command": failure.get("command"),
                "workdir": failure.get("workdir"),
                "exit_code": failure.get("exit_code"),
                "verification_issue": failure.get("verification_issue"),
                "stderr": failure.get("stderr"),
                "status": "open",
                "notes": f"Recovery attempts: {failure.get('recovery_attempts', 0)}",
            }
        )
    for path in incomplete_sources:
        reasons: List[str] = []
        if path in unstarted_sources:
            reasons.append("unstarted")
        if path in iterations_exhausted_sources:
            reasons.append("iterations exhausted")
        if path in coverage_missing_sources:
            reasons.append("requirement coverage missing")
        if max_steps_reached:
            reasons.append("max steps reached")
        todo_items.append(
            {
                "type": "incomplete_source",
                "source": path,
                "status": "open",
                "notes": ", ".join(reasons) if reasons else "incomplete",
            }
        )
    for source in requirement_sources:
        for todo_line in source.todo_lines or []:
            todo_items.append(
                {
                    "type": "source_todo",
                    "source": source.path,
                    "status": "open",
                    "notes": todo_line.strip(),
                }
            )
    for path, coverage in requirement_coverage.items():
        missing = coverage.get("missing_requirements") if isinstance(coverage, dict) else None
        if missing:
            todo_items.append(
                {
                    "type": "missing_requirement_coverage",
                    "source": path,
                    "status": "open",
                    "notes": "; ".join(missing[:20]) + ("; ...(truncated)" if len(missing) > 20 else ""),
                }
            )
    if requirements_missing_ids:
        todo_items.append(
            {
                "type": "requirements_sanity_missing",
                "source": "requirements_register",
                "status": "open",
                "notes": "Missing requirement IDs: "
                + ", ".join(requirements_missing_ids[:20])
                + (" ...(truncated)" if len(requirements_missing_ids) > 20 else ""),
            }
        )
    for gap in sequence_gaps:
        prefix = _safe_str(gap.get("prefix"))
        missing = gap.get("missing") if isinstance(gap.get("missing"), list) else []
        if not prefix or not missing:
            continue
        todo_items.append(
            {
                "type": "sequence_gap",
                "source": "sequence_check",
                "status": "open",
                "notes": f"Missing sequence items for '{prefix}': "
                + ", ".join(f"{prefix}{num}" for num in missing),
            }
        )
    todo_items = _dedupe_todo_items(todo_items)
    todo_summary = {
        "items": todo_items,
        "counts": {
            "total": len(todo_items),
            "unresolved_failures": len(unresolved_failures),
            "unresolved_verification_failures": len(unresolved_verification_failures),
            "incomplete_sources": len(incomplete_sources),
            "source_todos": sum(1 for item in todo_items if item.get("type") == "source_todo"),
            "missing_requirement_coverage": sum(
                1 for item in todo_items if item.get("type") == "missing_requirement_coverage"
            ),
            "sequence_gaps": sum(1 for item in todo_items if item.get("type") == "sequence_gap"),
            "requirements_sanity_missing": sum(
                1 for item in todo_items if item.get("type") == "requirements_sanity_missing"
            ),
        },
    }
    actions_log.append(
        f"TODO summary: {todo_summary['counts']['total']} items "
        f"({todo_summary['counts']['unresolved_failures']} unresolved failures)."
    )

    output_data = {
        "summary": all_plans[-1].get("summary") if all_plans else None,
        "requirements": all_plans[-1].get("requirements") if all_plans else None,
        "plans": all_plans,
        "derived_requirements": [
            {
                "path": src.path,
                "requirements_text": src.requirements_text,
                "requirement_lines": src.requirement_lines,
                "todo_lines": src.todo_lines,
                "context_excerpt": src.context_excerpt,
            }
            for src in requirement_sources
        ],
        "requirements_register": requirements_register,
        "requirements_register_source": requirements_register_source,
        "requirements_register_index": requirements_register_index,
        "requirements_register_markdown": requirements_register_markdown,
        "requirements_sanity": requirements_sanity,
        "sequence_gaps": sequence_gaps,
        "dataset_summary": dataset_summary,
        "helper_modules": helper_modules,
        "todo_summary": todo_summary,
        "todo_table_markdown": _format_todo_markdown(todo_summary),
        "actions_log": actions_log,
        "solver_workspace": solver_workspace_rel,
        "solver_workspace_explicit": output_dir_explicit,
        "solver_workspace_implied": output_dir_implied,
        "solver_workspace_preexisting": workspace_preexisting,
        "solver_workspace_within_project": solver_workspace_within_project,
        "prefer_workspace_new_files": prefer_workspace_new_files,
        "requirement_source_count": len(requirement_sources),
        "hallucinated_requirements": {
            path: sorted(set(items))
            for path, items in hallucinations_by_source.items()
            if items
        },
        "requirement_coverage": requirement_coverage,
        "requirement_traceability": requirement_traceability,
        "llm_inadequate_counts": {path: count for path, count in inadequate_counts.items() if count},
        "opencode_fallback_sources": sorted(opencode_used_sources),
        "codingagent_used_sources": {
            agent: sorted(paths) for agent, paths in codingagent_used_sources.items() if paths
        },
        "audit_reports": audit_reports,
        "audit_config": {
            "enabled": audit_enabled,
            "every_iterations": audit_every_iterations,
            "every_steps": audit_every_steps,
            "agent_mode": audit_agent_mode,
            "log_path": audit_log_path,
        },
        "agentic_workflow": agentic_workflow.export(),
        "progress_tracker": progress_tracker.export(),
        "web_research_reports": web_research_reports,
        "web_research_config": {
            "enabled": web_research_enabled,
            "mode": web_research_mode,
            "every_iterations": web_research_every_iterations,
            "every_steps": web_research_every_steps,
            "max_queries": web_research_max_queries,
            "max_results": web_research_max_results,
            "cache_ttl_hours": web_research_cache_ttl_hours,
        },
        "repo_context_config": {
            "enabled": repo_rag_enabled,
            "stats": repo_index.stats() if repo_index else {},
        },
        "codex_preflight": codex_preflight,
        "completion_summary": completion_summary,
        "run_config": {
            "project_root": project_root,
            "requirements_path": requirements_path,
            "output_path": output_path,
            "project_output_dir": project_output_dir,
            "max_steps": max_steps,
            "max_iterations": max_iterations,
            "verification_first": _env_bool("SOLVER_VERIFICATION_FIRST", True),
            "codingagent": codingagent_primary or "llm",
            "codingagent_fallback": codingagent_fallback_norm or "llm",
            "codingagent_mode": codingagent_primary_mode,
            "codingagent_model": codingagent_model,
            "codingagent_reasoning_effort": codingagent_reasoning_effort,
            "llm_reasoning_effort": llm_reasoning_effort,
            "fallback_llm_provider": fallback_llm_provider,
            "fallback_llm_model": fallback_llm_model,
            "agentic_roles": {
                role: {k: v for k, v in cfg.items() if k != "api_key"}
                for role, cfg in role_configs.items()
            },
        },
        "resume": {
            "used": resume_used,
            "source": resume_source_path,
            "start_iteration": resume_start_iteration.get(resume_source_path) if resume_source_path else None,
            "completed_sources": sorted(set(completed_sources)),
        },
    }
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(output_data, handle, indent=2)

    cleanup = getattr(provider, "cleanup", None)
    if callable(cleanup):
        try:
            cleanup()
        except Exception:
            pass

    return 0
