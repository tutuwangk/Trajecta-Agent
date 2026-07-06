from app.api import routes


def test_sync_precise_transport_edges_rebuilds_selected_branch_route(monkeypatch):
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "喜茶(武侯祠店)", "selected_branch_id": "H2"},
                    {"poi_id": "p2", "name": "东郊记忆"},
                ],
            }
        ]
    }
    runtime_pois = [
        {
            "poi_id": "p1",
            "standard_name": "喜茶（待选择）",
            "location": {"lng": 104.0805, "lat": 30.6572},
            "route_branch_options": [
                {"branch_id": "H2", "name": "喜茶(武侯祠店)", "location": {"lng": 104.047, "lat": 30.645}},
            ],
        },
        {
            "poi_id": "p2",
            "standard_name": "东郊记忆",
            "location": {"lng": 104.121, "lat": 30.641},
        },
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 55, "distance_m": 19000}
    ]

    def fake_build_route_edge(origin, destination, amap_client, user_profile):
        if origin.get("amap_id") == "H2" and destination.get("poi_id") == "p2":
            return {"mode": "walking", "duration_min": 8, "distance_m": 600}
        return {"mode": "taxi", "duration_min": 55, "distance_m": 19000}

    monkeypatch.setattr(routes, "build_route_edge", fake_build_route_edge)

    routes._sync_precise_transport_edges(itinerary, runtime_pois, route_matrix, {"constraints": {}}, object())

    assert itinerary["days"][0]["items"][0]["transport_to_next"] == {
        "mode": "walking",
        "duration_min": 8,
        "distance_m": 600,
    }
