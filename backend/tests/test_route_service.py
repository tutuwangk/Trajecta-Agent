from app.services.route_service import build_route_matrix, build_spatial_route_matrix, classify_relation


def test_classify_relation_uses_duration_and_mode():
    assert classify_relation("walking", 9) == "same_cluster"
    assert classify_relation("walking", 20) == "nearby"
    assert classify_relation("driving", 35) == "same_day_possible"
    assert classify_relation("driving", 55) == "separate_day"
    assert classify_relation("walking", None) == "unknown"


def test_build_route_matrix_uses_walking_within_two_km_even_if_it_takes_longer():
    runtime_pois = [_poi("p1"), _poi("p2")]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(30, 1700), driving=(12, 2500), transit=(26, 2300)),
        user_profile={"transport_preference": ["walking", "taxi", "public_transport"]},
    )

    edge = matrix[0]
    assert edge["mode"] == "walking"
    assert edge["duration_min"] == 30
    assert edge["distance_m"] == 1700


def test_build_route_matrix_uses_walking_within_two_km_before_public_transport_preference():
    runtime_pois = [_poi("p1"), _poi("p2")]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(22, 1200), driving=(8, 1900), transit=(18, 1600)),
        user_profile={"transport_preference": ["public_transport", "taxi"]},
    )

    edge = matrix[0]
    assert edge["mode"] == "walking"
    assert edge["duration_min"] == 22
    assert edge["distance_m"] == 1200


def test_build_route_matrix_defaults_to_taxi_beyond_two_km_without_user_preference():
    runtime_pois = [_poi("p1"), _poi("p2")]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(35, 2100), driving=(9, 2600), transit=(21, 2400)),
        user_profile={},
    )

    edge = matrix[0]
    assert edge["mode"] == "taxi"
    assert edge["duration_min"] == 9
    assert edge["distance_m"] == 2600


def test_build_route_matrix_respects_public_transport_preference_beyond_two_km():
    runtime_pois = [_poi("p1"), _poi("p2")]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(35, 2100), driving=(8, 2600), transit=(18, 2300)),
        user_profile={"transport_preference": ["public_transport", "taxi"]},
    )

    edge = matrix[0]
    assert edge["mode"] == "public_transport"
    assert edge["duration_min"] == 18


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


def test_build_route_matrix_excludes_unresolved_chain_even_if_it_has_coordinates():
    runtime_pois = [
        _poi("p1"),
        {
            **_poi("p2"),
            "standard_name": "喜茶（待选择）",
            "match_status": "ambiguous",
            "is_chain": True,
            "chain_status": "unresolved",
            "user_override": "none",
        },
    ]

    matrix = build_route_matrix(
        runtime_pois,
        FakeAmap(walking=(12, 900), driving=(8, 1500), transit=(18, 1600)),
        user_profile={"transport_preference": ["walking", "taxi", "public_transport"]},
    )

    assert matrix == []


def test_build_spatial_route_matrix_uses_coordinates_without_direction_api_calls():
    runtime_pois = [_poi("p1"), _poi("p2")]

    matrix = build_spatial_route_matrix(runtime_pois, user_profile={"transport_preference": ["taxi"]})

    assert len(matrix) == 2
    assert all(edge["source"] == "spatial_estimate" for edge in matrix)
    assert all(edge["distance_m"] > 0 for edge in matrix)
    assert all(edge["duration_min"] > 0 for edge in matrix)


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
