"""Refiner public package API.

Keep package import side effects light while preserving the small legacy surface
area that callers still import from `refiner` directly.
"""

from __future__ import annotations

from importlib import import_module
import sys
from types import ModuleType


_MAIN_EXPORTS = (
    "analyze_issue_transitions",
    "get_monthly_worklog_times",
    "seconds_to_work_units",
    "normalize_name",
    "sorting_key",
)

# A small compatibility bridge for older patch/import targets used by tests and
# local scripts after the root-level module migration into `refiner/`.
_LEGACY_ROOT_MODULE_ALIASES = {
    "file_converter": "refiner.file_converter",
    "llm_providers": "refiner.llm_providers",
    "platform_selector": "refiner.platform_selector",
    "refiner_ai_model_inventory": "refiner.refiner_ai_model_inventory",
}


def _load_main_module() -> ModuleType:
    module = globals().get("main")
    if isinstance(module, ModuleType):
        return module
    module = import_module(".main", __name__)
    globals()["main"] = module
    return module


def _register_legacy_root_aliases() -> None:
    for alias, target in _LEGACY_ROOT_MODULE_ALIASES.items():
        if alias in sys.modules:
            continue
        try:
            sys.modules[alias] = import_module(target)
        except Exception:
            # Leave unresolved aliases to fail naturally when optional
            # dependencies for that module are unavailable.
            continue


def __getattr__(name: str):
    if name == "main":
        return _load_main_module()
    if name in _MAIN_EXPORTS:
        value = getattr(_load_main_module(), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_register_legacy_root_aliases()

__all__ = ["main", *_MAIN_EXPORTS]
