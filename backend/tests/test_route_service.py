from app.services.route_service import build_route_matrix, classify_relation


def test_classify_relation_uses_duration_and_mode():
    assert classify_relation("walking", 9) == "same_cluster"
    assert classify_relation("walking", 20) == "nearby"
    assert classify_relation("driving", 35) == "same_day_possible"
    assert classify_relation("driving", 55) == "separate_day"
    assert classify_relation("walking", None) == "unknown"


def test_build_route_matrix_does_not_label_long_walk_as_walking():
    runtime_pois = [_poi("p1"), _poi("p2")]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(30, 1700), driving=(12, 2500), transit=(26, 2300)),
        user_profile={"transport_preference": ["walking", "taxi", "public_transport"]},
    )

    edge = matrix[0]
    assert edge["mode"] == "taxi"
    assert edge["duration_min"] == 12
    assert edge["distance_m"] == 2500


def test_build_route_matrix_respects_public_transport_preference_when_available():
    runtime_pois = [_poi("p1"), _poi("p2")]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(22, 1200), driving=(8, 1900), transit=(18, 1600)),
        user_profile={"transport_preference": ["public_transport", "taxi"]},
    )

    edge = matrix[0]
    assert edge["mode"] == "public_transport"
    assert edge["duration_min"] == 18
    assert edge["distance_m"] == 1600


def test_build_route_matrix_includes_user_confirmed_ambiguous_map_candidate():
    runtime_pois = [
        _poi("p1"),
        {
            **_poi("p2"),
            "match_status": "ambiguous",
            "amap_id": "B004",
            "user_override": "must_include",
        },
    ]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(12, 900), driving=(8, 1500), transit=(18, 1600)),
        user_profile={"transport_preference": ["walking", "taxi", "public_transport"]},
    )

    assert {(edge["origin_poi_id"], edge["destination_poi_id"]) for edge in matrix} == {("p1", "p2"), ("p2", "p1")}


def _poi(poi_id: str) -> dict:
    index = 1 if poi_id == "p1" else 2
    return {
        "poi_id": poi_id,
        "match_status": "matched",
        "city": "北京",
        "location": {"lng": 116.3 + index / 100, "lat": 39.9 + index / 100},
    }


class FakeAmap:
    def __init__(self, walking: tuple[int, int], driving: tuple[int, int], transit: tuple[int, int]):
        self.walking = walking
        self.driving = driving
        self.transit = transit

    def walking_direction(self, origin, destination):
        return _direction(*self.walking)

    def driving_direction(self, origin, destination):
        return _direction(*self.driving)

    def transit_direction(self, origin, destination, city):
        return {"route": {"transits": [{"duration": str(self.transit[0] * 60), "distance": str(self.transit[1])}]}}


def _direction(duration_min: int, distance_m: int) -> dict:
    return {"route": {"paths": [{"duration": str(duration_min * 60), "distance": str(distance_m)}]}}
