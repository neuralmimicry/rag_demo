"""Minimal local odfpy compatibility shim for test environments."""

from . import text, teletype  # noqa: F401
from .opendocument import OpenDocumentText, load  # noqa: F401
