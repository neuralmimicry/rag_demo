from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
import ast
import os
import re


DEFAULT_IGNORED_DIRS = {
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
    "delivery_pipeline_output",
}

CODE_EXTS = {
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


def _tokenize(text: str) -> Set[str]:
    if not text:
        return set()
    cleaned = re.sub(r"[^-A-Za-z0-9_/\\]", " ", text)
    tokens = set()
    for chunk in cleaned.split():
        lower = chunk.strip().lower()
        if len(lower) < 3:
            continue
        tokens.add(lower)
        for part in re.split(r"[-_/]", lower):
            if len(part) >= 3:
                tokens.add(part)
    return tokens


def _safe_read(path: str, max_bytes: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(max_bytes)
    except Exception:
        return ""


def _extract_python_symbols(text: str) -> List[str]:
    try:
        tree = ast.parse(text)
    except Exception:
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
    return sorted({n for n in names if n})


def _extract_js_symbols(text: str) -> List[str]:
    names = set()
    for match in re.finditer(r"\b(class|function)\s+([A-Za-z_][A-Za-z0-9_]*)", text):
        names.add(match.group(2))
    for match in re.finditer(r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s+)?\\(?", text):
        names.add(match.group(1))
    for match in re.finditer(r"\bexport\s+(?:default\s+)?(?:class|function)\s+([A-Za-z_][A-Za-z0-9_]*)", text):
        names.add(match.group(1))
    return sorted(names)


def _extract_symbols(path: str, text: str) -> List[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return _extract_python_symbols(text)
    if ext in {".js", ".jsx", ".ts", ".tsx"}:
        return _extract_js_symbols(text)
    return []


@dataclass
class RepoFileSummary:
    path: str
    ext: str
    size: int
    symbols: List[str]
    excerpt: str
    tokens: Set[str]


class RepoIndex:
    def __init__(self, root: str, files: List[RepoFileSummary]):
        self.root = root
        self.files = files

    @classmethod
    def build(
        cls,
        root: str,
        *,
        extra_ignored: Optional[Iterable[str]] = None,
        max_files: int = 300,
        max_file_bytes: int = 200_000,
    ) -> "RepoIndex":
        ignored = set(DEFAULT_IGNORED_DIRS)
        if extra_ignored:
            ignored.update({p for p in extra_ignored if p})
        summaries: List[RepoFileSummary] = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ignored]
            rel_dir = os.path.relpath(dirpath, root)
            for filename in files:
                if len(summaries) >= max_files:
                    break
                rel_path = os.path.normpath(os.path.join(rel_dir, filename))
                if rel_path.startswith(".."):
                    continue
                abs_path = os.path.join(root, rel_path)
                try:
                    size = os.path.getsize(abs_path)
                except Exception:
                    continue
                if size > max_file_bytes:
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext and ext not in CODE_EXTS and ext not in {".md", ".txt", ".rst", ".json", ".yml", ".yaml", ".toml"}:
                    continue
                text = _safe_read(abs_path, max_bytes=max_file_bytes)
                if not text:
                    continue
                symbols = _extract_symbols(abs_path, text)
                excerpt_lines = [line.strip() for line in text.splitlines()[:8] if line.strip()]
                excerpt = "\n".join(excerpt_lines[:4])
                tokens = _tokenize(rel_path)
                tokens |= _tokenize(" ".join(symbols))
                tokens |= _tokenize(excerpt)
                summaries.append(
                    RepoFileSummary(
                        path=rel_path,
                        ext=ext,
                        size=size,
                        symbols=symbols,
                        excerpt=excerpt,
                        tokens=tokens,
                    )
                )
            if len(summaries) >= max_files:
                break
        return cls(root, summaries)

    def stats(self) -> Dict[str, int]:
        ext_counts: Dict[str, int] = {}
        for item in self.files:
            ext_counts[item.ext] = ext_counts.get(item.ext, 0) + 1
        return {
            "file_count": len(self.files),
            "code_files": sum(1 for item in self.files if item.ext in CODE_EXTS),
            "ext_count": len(ext_counts),
        }

    def search(self, query: str, limit: int = 6) -> List[RepoFileSummary]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scored: List[Tuple[int, RepoFileSummary]] = []
        for item in self.files:
            overlap = len(query_tokens & item.tokens)
            if overlap == 0:
                continue
            scored.append((overlap, item))
        scored.sort(key=lambda x: (-x[0], x[1].path))
        return [item for _, item in scored[:limit]]

    @staticmethod
    def format_matches(matches: List[RepoFileSummary], *, max_symbols: int = 6) -> str:
        if not matches:
            return ""
        lines = ["Repo context (likely relevant files):"]
        for match in matches:
            symbol_text = ""
            if match.symbols:
                symbol_text = f" symbols: {', '.join(match.symbols[:max_symbols])}"
            lines.append(f"- {match.path}{symbol_text}")
            if match.excerpt:
                lines.append(f"  Excerpt: {match.excerpt[:240]}")
        return "\n".join(lines).strip()
