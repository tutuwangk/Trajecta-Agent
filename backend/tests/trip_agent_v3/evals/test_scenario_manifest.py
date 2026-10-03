from __future__ import annotations

import ast
import json
from pathlib import Path


def test_real_journey_manifest_has_unique_executable_acceptance_cases() -> None:
    manifest_path = Path(__file__).with_name("scenario_matrix.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]

    assert payload["schema_version"] == 3
    assert len(scenarios) >= 18
    assert len({item["scenario_id"] for item in scenarios}) == len(scenarios)
    assert all(item["user_risk"] for item in scenarios)
    assert all(item["expected_delivery_state"] for item in scenarios)
    for scenario in scenarios:
        test_path, separator, test_name = scenario["pytest_nodeid"].partition(
            "::"
        )
        assert separator == "::"
        assert test_name.startswith("test_")
        source_path = Path(test_path)
        assert source_path.is_file()
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        function_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert test_name in function_names
