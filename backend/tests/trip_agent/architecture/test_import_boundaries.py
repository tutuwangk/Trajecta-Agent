from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_MODULES = {
    "app.agents.planning_workflow",
    "app.agents.planner",
    "app.agents.intent_ledger",
    "app.agents.poi_extractor",
    "app.agents.schedule_evaluator",
    "app.agents.itinerary_normalizer",
    "app.services.poi_grounder",
    "app.orchestration.planning_orchestrator",
}


def test_trip_agent_does_not_import_legacy_decision_modules():
    package = Path(__file__).resolve().parents[3] / "app" / "trip_agent"
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
                if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_MODULES):
                    violations.append(f"{path.relative_to(package)} imports {module}")
    assert violations == []
