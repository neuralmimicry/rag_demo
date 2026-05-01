"""Compatibility wrapper for the reorganised Refiner package layout."""

from importlib import import_module as _import_module
import sys as _sys

_impl = _import_module("refiner.runtime.frontend_server")
if __name__ == "__main__":
    raise SystemExit(getattr(_impl, "main")())
if __name__ != "__main__":
    _sys.modules[__name__] = _impl
