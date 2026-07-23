from __future__ import annotations

from types import SimpleNamespace

from scripts.live_acceptance_six import SCENARIOS
from scripts.run_trip_agent_v2_wp8 import _fixture_answers, _resolved_expected_ids, _summary


def _record(case_id: str, status: str, **updates) -> dict:
    record = {
        "case_id": case_id,
        "attempt_id": f"attempt-{case_id}-{status}",
        "previous_attempt_id": None,
        "elapsed_seconds": 10,
        "status": status,
        "unhandled_exception": status == "unhandled_exception",
        "product_checks_passed": status == "published",
        "deterministic_fallback_detected": False,
        "provider_retry_count": 0,
        "provider_recovery_count": 0,
        "checkpoint_publish_count": 0,
        "cache_hit_count": 0,
    }
    record.update(updates)
    return record


def test_wp8_summary_counts_every_run_state_and_internal_failure_blocks_gate():
    records = [
        _record("published", "published"),
        _record("incomplete", "incomplete", failure_class="transient_external"),
        _record("failed", "failed", failure_class="internal"),
        _record("waiting", "waiting_user", waiting_user_unresolved=True),
        _record("cancelled", "cancelled"),
        _record("unhandled", "unhandled_exception"),
    ]

    summary = _summary(records, None)

    assert summary["status_counts"] == {
        "cancelled": 1,
        "failed": 1,
        "incomplete": 1,
        "published": 1,
        "unhandled_exception": 1,
        "waiting_user": 1,
    }
    assert summary["failed_count"] == 1
    assert summary["waiting_user_count"] == 1
    assert summary["cancelled_count"] == 1
    assert summary["unhandled_exception_count"] == 1
    assert summary["gate_passed"] is False


def test_wp8_summary_preserves_first_failure_but_uses_latest_attempt_for_product_gate():
    first = _record(
        "beijing",
        "incomplete",
        failure_class="transient_external",
        product_checks_passed=False,
    )
    recovered = _record(
        "beijing",
        "published",
        previous_attempt_id=first["attempt_id"],
        product_checks_passed=True,
    )

    summary = _summary([first, recovered], None)

    assert summary["first_provider_error_count"] == 1
    assert summary["latest_case_product_pass_count"] == 1
    assert summary["gate_passed"] is True


def test_wp8_summary_retains_repaired_internal_failure_without_poisoning_latest_gate():
    first = _record(
        "chengdu",
        "failed",
        failure_class="internal",
        product_checks_passed=False,
    )
    repaired = _record(
        "chengdu",
        "published",
        previous_attempt_id=first["attempt_id"],
        product_checks_passed=True,
    )

    summary = _summary([first, repaired], None)

    assert summary["internal_failure_count"] == 1
    assert summary["latest_internal_failure_count"] == 0
    assert summary["gate_passed"] is True


def test_wp8_mvp_gate_requires_six_scenarios_but_allows_one_honest_incomplete():
    records = [
        _record(
            scenario["id"],
            "published" if index < 5 else "incomplete",
            product_checks_passed=index < 5,
            failure_class=None if index < 5 else "transient_external",
            latency_hard_limit_seconds=720,
        )
        for index, scenario in enumerate(SCENARIOS)
    ]

    summary = _summary(records, None, gate_profile="mvp")

    assert summary["mvp_gate_evaluated"] is True
    assert summary["mvp_gate_passed"] is True
    assert summary["gate_passed"] is True
    assert summary["mvp_published_count"] == 5
    assert summary["latest_publish_rate"] == 5 / 6


def test_wp8_cutover_gate_remains_unmet_for_mvp_sample():
    records = [
        _record(
            scenario["id"],
            "published",
            product_checks_passed=True,
            latency_hard_limit_seconds=720,
        )
        for scenario in SCENARIOS
    ]

    summary = _summary(records, None, gate_profile="cutover")

    assert summary["mvp_gate_passed"] is True
    assert summary["cutover_gate_evaluated"] is False
    assert summary["gate_passed"] is False


def test_structured_clarification_fixture_resumes_only_known_questions():
    interruption = SimpleNamespace(
        questions=(
            SimpleNamespace(
                question_id="restaurant_bianyi_fang",
                prompt="请选择便宜坊的具体分店",
                options=("前门店", "王府井店"),
            ),
            SimpleNamespace(
                question_id="restaurant_zhajiangmian",
                prompt="请选择炸酱面位置",
                options=(),
            ),
        )
    )
    scenario = {"id": "beijing_3d_high_culture"}

    assert _fixture_answers(scenario, interruption) == {
        "restaurant_bianyi_fang": "前门店",
        "restaurant_zhajiangmian": "无所谓，由你安排最方便的",
    }
    unknown = SimpleNamespace(
        questions=(
            SimpleNamespace(question_id="q2", prompt="你想几点出发？", options=("8点",)),
        )
    )
    assert _fixture_answers(scenario, unknown) is None


def test_expected_place_identity_follows_commitment_subject_not_name_substring():
    workspace = SimpleNamespace(
        goal_ledger=SimpleNamespace(
            commitments=(
                SimpleNamespace(
                    value="外滩",
                    evidence_text="外滩",
                    subject_hypothesis_id="hypothesis-bund",
                ),
            )
        ),
        place_hypotheses=(
            SimpleNamespace(hypothesis_id="hypothesis-bund", raw_name="外滩"),
            SimpleNamespace(
                hypothesis_id="hypothesis-hotel",
                raw_name="上海外滩英迪格酒店",
            ),
        ),
        place_resolutions=(
            SimpleNamespace(hypothesis_id="hypothesis-bund", candidate_id="candidate-bund"),
            SimpleNamespace(hypothesis_id="hypothesis-hotel", candidate_id="candidate-hotel"),
        ),
    )

    candidate_ids, unresolved = _resolved_expected_ids(workspace, ["外滩"])

    assert candidate_ids == {"candidate-bund"}
    assert unresolved == []
