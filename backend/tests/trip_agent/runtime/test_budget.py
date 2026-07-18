from __future__ import annotations

from app.trip_agent.budget import RuntimeBudget


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
