from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_PREFIXES = (
    "app.agents",
    "app.api",
    "app.domain",
    "app.orchestration",
    "app.planning",
    "app.services",
    "app.trip_agent",
)


def _matches_package(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def test_trip_agent_v3_does_not_import_legacy_stacks() -> None:
    package = Path(__file__).resolve().parents[3] / "app" / "trip_agent_v3"
    violations: list[str] = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for module in imported:
                if any(_matches_package(module, prefix) for prefix in FORBIDDEN_PREFIXES):
                    violations.append(f"{path.relative_to(package)} imports {module}")
    assert violations == []
