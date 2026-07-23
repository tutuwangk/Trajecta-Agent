from __future__ import annotations

from app.trip_agent.budget import RuntimeBudget
from pydantic_ai.usage import RunUsage


def test_progress_monitor_stops_expansion_after_three_non_improving_simulations():
    budget = RuntimeBudget()
    budget.observe_simulation(3)
    budget.observe_simulation(3)
    budget.observe_simulation(4)
    budget.observe_simulation(3)

    denial = budget.authorize("search_place_candidates", "new-query")
    assert denial is not None
    assert "progress monitor rejected expansion" in denial

    # Convergence actions remain available so the run can still repair and submit.
    assert budget.authorize("apply_draft_change", "repair-v4") is None
    assert budget.authorize("submit_candidate", "v5") is None


def test_fewer_blockers_resets_non_progress_counter():
    budget = RuntimeBudget()
    budget.observe_simulation(4)
    budget.observe_simulation(4)
    budget.observe_simulation(4)
    budget.observe_simulation(2)

    assert budget.authorize("search_place_candidates", "useful-query") is None
    assert budget.simulation_no_progress == 0


def test_reserve_mode_defers_expansion_without_ending_convergence():
    budget = RuntimeBudget(max_seconds=100)
    budget.started_at -= 71

    denial = budget.authorize("estimate_visit_profile", "candidate-a")

    assert denial is not None
    assert "reserve mode" in denial
    assert budget.authorize("simulate_candidate", "draft-v2") is None
    assert budget.authorize("submit_candidate", "draft-v2") is None


def test_budget_state_restores_elapsed_time_and_progress_counters():
    original = RuntimeBudget(carried_seconds=90)
    original.observe_simulation(3)
    original.authorize("search_place_candidates", "h1")

    restored = RuntimeBudget.from_state(original.snapshot())

    assert restored.elapsed_seconds >= 90
    assert restored.last_blocker_count == 3
    assert restored.signatures["search_place_candidates:h1"] == 1


def test_expansion_is_soft_limited_until_an_early_draft_exists():
    budget = RuntimeBudget(max_repeated_signature=20)

    for index in range(12):
        assert budget.authorize("search_place_candidates", f"h{index}") is None
    denial = budget.authorize("compare_place_candidates", "h13")

    assert denial is not None
    assert "early-draft reserve" in denial
    assert budget.authorize("apply_draft_change", "draft-1") is None
    budget.observe_draft()
    assert budget.authorize("search_place_candidates", "focused-follow-up") is None


def test_two_complete_checkpoints_force_submission_instead_of_reopening_route():
    budget = RuntimeBudget()
    budget.observe_complete_checkpoint()
    assert budget.authorize("apply_draft_change", "one-focused-improvement") is None

    budget.observe_complete_checkpoint()

    denial = budget.authorize("hydrate_draft_context", "draft-v3")
    assert denial is not None
    assert "submit_candidate now" in denial
    assert budget.authorize("submit_candidate", "draft-v2") is None


def test_convergence_hides_discovery_but_keeps_repair_and_publication():
    budget = RuntimeBudget()
    budget.enter_convergence()

    assert "convergence episode" in (
        budget.authorize("search_candidate_sets", "all-open") or ""
    )
    assert budget.authorize("hydrate_draft_context", "draft-v1") is None
    assert budget.authorize("simulate_candidate", "draft-v1") is None
    assert budget.authorize("submit_candidate", "draft-v1") is None


def test_provider_usage_is_carried_across_episodes():
    budget = RuntimeBudget()
    budget.observe_run_usage(
        RunUsage(requests=4, tool_calls=9, input_tokens=1200, output_tokens=300)
    )
    restored = RuntimeBudget.from_state(budget.snapshot())

    assert restored.run_usage().requests == 4
    assert restored.run_usage().tool_calls == 9
    assert restored.run_usage().input_tokens == 1200
    assert restored.run_usage().output_tokens == 300


def test_crashed_episode_wall_clock_is_carried_into_recovery(monkeypatch):
    clock = {"value": 1_000.0}
    monkeypatch.setattr("app.trip_agent.budget.time", lambda: clock["value"])
    budget = RuntimeBudget(carried_seconds=40)
    budget.begin_episode()
    state = budget.snapshot()
    clock["value"] += 25

    restored = RuntimeBudget.from_state(state)

    assert restored.elapsed_seconds >= 65
    assert restored.active_episode_started_epoch is None
