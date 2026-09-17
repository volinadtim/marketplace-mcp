"""The core must not import an adapter.

This is the whole point of the split and the easiest thing to undo by accident:
one convenient import from core/store into adapters/ozon and a second
marketplace can no longer be added without touching the first. A grep is worth
more than a convention here, because the convention is invisible at the moment
someone breaks it.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "marketplace_mcp" / "core"


def _imported_modules(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_module_in_the_core_imports_an_adapter() -> None:
    offenders = {
        module.relative_to(CORE).as_posix(): sorted(name for name in _imported_modules(module) if "adapters" in name)
        for module in CORE.rglob("*.py")
    }
    assert {path: names for path, names in offenders.items() if names} == {}


def test_the_core_is_importable_on_its_own() -> None:
    """Nothing in the core may need an adapter present to be imported.

    A cycle hidden behind a function-level import would pass the check above
    and still leave the core unusable without Ozon installed. Imported by name
    so the check is a call, not an import statement pinned to this function.
    """
    modules = ("records", "source", "store.dedup", "store.schema", "store.sync", "store.writes")
    loaded = [importlib.import_module(f"marketplace_mcp.core.{name}") for name in modules]
    assert len(loaded) == len(modules)
