from scripts.validate_agent_v2_datasets import score_mentions


def test_provider_errors_reduce_successful_coverage_and_overall_recall():
    cases = [
        {"case_id": "m1", "expected": {"mentions": ["武侯祠"]}},
        {"case_id": "m2", "expected": {"mentions": ["杜甫草堂"]}},
    ]
    attempts = {
        "m1": [{"case_id": "m1", "prediction": {"mentions": ["武侯祠"]}}],
        "m2": [
            {
                "case_id": "m2",
                "prediction": {},
                "error_type": "TimeoutError",
            }
        ],
    }

    score = score_mentions(cases, attempts)

    assert score["attempted_coverage"] == 1
    assert score["successful_coverage"] == 0.5
    assert score["first_pass_error_rate"] == 0.5
    assert score["recall"] == 0.5


def test_retry_is_reported_as_recovery_without_erasing_first_pass_error():
    cases = [{"case_id": "m1", "expected": {"mentions": ["武侯祠"]}}]
    attempts = {
        "m1": [
            {"case_id": "m1", "prediction": {}, "error_type": "TimeoutError"},
            {"case_id": "m1", "prediction": {"mentions": ["武侯祠"]}},
        ]
    }

    score = score_mentions(cases, attempts)

    assert score["successful_coverage"] == 1
    assert score["first_pass_error_rate"] == 1
    assert score["recovered_case_count"] == 1
    assert score["recall"] == 1
