from app.adapters.legacy import planning_context_from_legacy
from app.domain.planning import PlannerFactRequest
from app.planning.poi_fact_resolver import POIFactResolver, apply_fact_resolution
from app.services.web_search import WebSearchResult


class FakeSearch:
    def search(self, query, *, max_results=5):
        assert "2026-07-20" in query
        return [
            WebSearchResult(
                title="成都博物馆参观公告",
                url="https://www.cdmuseum.com/visit",
                snippet="周一闭馆，周二至周日09:00-17:00开放。",
            )
        ]


class FakeFactLLM:
    call_metrics = []

    def json_chat(self, messages, step, temperature=0.0):
        assert step == "extract_poi_availability_fact"
        return {
            "status": "closed",
            "open_intervals": [],
            "last_entry_time": "",
            "source_index": 0,
            "evidence_summary": "公告说明周一闭馆。",
        }


def test_fact_resolver_returns_date_scoped_source_backed_fact_and_applies_it():
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都博物馆", "match_status": "matched", "city": "成都"}
    ]
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "plannable_pois": [{"poi_id": "p1", "name": "成都博物馆"}],
            "must_poi_ids": ["p1"],
        },
        {"destination": "成都", "days": 1, "start_date": "2026-07-20", "constraints": {}},
        runtime_pois,
        [],
    )
    request = PlannerFactRequest(
        request_id="hours-p1",
        poi_id="p1",
        kind="opening_hours",
        visit_date="2026-07-20",
        decision_reason="开放状态决定是否换天。",
    )

    batch = POIFactResolver(FakeSearch(), FakeFactLLM()).resolve([request], context)
    apply_fact_resolution(runtime_pois, batch)

    assert batch.availability_facts[0].status == "closed"
    assert batch.availability_facts[0].confidence == "estimated"
    assert runtime_pois[0]["availability_facts"][0]["visit_date"] == "2026-07-20"
