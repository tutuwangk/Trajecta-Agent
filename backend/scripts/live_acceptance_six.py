"""Run six realistic, mildly adversarial end-to-end acceptance scenarios."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.env import load_project_env
from scripts.live_acceptance import run_scenario


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "chengdu_1d_low_walk_food",
        "ugc": """成都一日游攻略：住成都太古里亚朵酒店。早上想去人民公园喝茶，上午逛宽窄巷子，下午去武侯祠和锦里，晚上不想排太久队。\n吃饭：午餐陈麻婆豆腐（青羊店），晚餐想在锦里附近解决。\n偏好：低强度、少折返，步行优先，远一点再打车。""",
        "raw_input": "成都一日游，住成都太古里亚朵酒店，低强度，步行优先，武侯祠必去，不要太赶。",
        "notes": "人民公园、鹤鸣茶社、宽窄巷子、武侯祠、锦里、陈麻婆豆腐（青羊店）。晚餐不要安排需要长时间排队的店。",
        "user_profile": {
            "destination": "成都",
            "start_date": "2026-08-03",
            "days": 1,
            "hotel_name": "成都太古里亚朵酒店",
            "route_goal": "food",
            "transport_preference": ["walking", "taxi"],
            "constraints": {"physical_intensity": "low", "must_visit": ["武侯祠"]},
        },
    },
    {
        "id": "shanghai_2d_medium_night",
        "ugc": """上海两日城市漫步：住上海外滩英迪格酒店。第一天以老城和博物馆为主，第二天安排田子坊和南京东路。\n外滩一定要晚上看灯光，最好 19:30 以后；上海博物馆东馆如果来不及可以放弃，但外滩不能删。\n餐饮：午餐南翔馒头店，晚餐绿波廊或附近本帮菜。交通以步行和地铁为主，打车只用于跨区。中等强度。""",
        "raw_input": "上海两日游，外滩安排在晚上，外滩必去，步行和地铁为主。",
        "notes": "上海博物馆东馆、豫园、城隍庙、南京东路步行街、外滩、田子坊、南翔馒头店、绿波廊。外滩要求 19:30 后。",
        "user_profile": {
            "destination": "上海",
            "start_date": "2026-08-10",
            "days": 2,
            "hotel_name": "上海外滩英迪格酒店",
            "route_goal": "citywalk",
            "transport_preference": ["walking", "public_transport", "taxi"],
            "constraints": {"physical_intensity": "medium", "must_visit": ["外滩"]},
        },
        "expected_time_windows": {"外滩": "night"},
    },
    {
        "id": "beijing_3d_high_culture",
        "ugc": """北京三日文化路线：住北京王府井文华东方酒店，特种兵强度，接受早起和地铁。\n第一天故宫、景山、天安门；第二天颐和园、圆明园；第三天八达岭长城。故宫和长城是重点，不要为了多塞景点牺牲交通时间。\n想吃一次便宜坊烤鸭和一次老北京炸酱面，午晚餐必须留出正常时间。""",
        "raw_input": "北京三日文化游，高强度，故宫和八达岭长城必去，地铁优先、远途可打车。",
        "notes": "故宫、景山公园、天安门广场、颐和园、圆明园、八达岭长城、便宜坊烤鸭、老北京炸酱面。接受早起，但不希望同一天市区和长城来回折返。",
        "user_profile": {
            "destination": "北京",
            "start_date": "2026-08-17",
            "days": 3,
            "hotel_name": "北京王府井文华东方酒店",
            "route_goal": "culture",
            "transport_preference": ["public_transport", "taxi", "walking"],
            "constraints": {"physical_intensity": "high", "must_visit": ["故宫", "八达岭长城"]},
        },
    },
    {
        "id": "xiamen_2d_low_island",
        "ugc": """厦门两日慢游：住厦门康莱德酒店。低强度，尽量步行和短途打车，不要每天排满。\n第一天南普陀寺、厦门大学、沙坡尾；第二天鼓浪屿和中山路。鼓浪屿希望留出半天以上，不要和太多岛外景点硬拼。\n吃饭：八市海鲜、沙坡尾小吃，海鲜只选一顿正餐。""",
        "raw_input": "厦门两日低强度慢游，住厦门康莱德酒店，鼓浪屿必去，步行优先。",
        "notes": "南普陀寺、厦门大学、沙坡尾艺术西区、鼓浪屿、中山路步行街、八市、亚本麦奶、1980烧肉粽。鼓浪屿至少安排半天。",
        "user_profile": {
            "destination": "厦门",
            "start_date": "2026-08-24",
            "days": 2,
            "hotel_name": "厦门康莱德酒店",
            "route_goal": "balanced",
            "transport_preference": ["walking", "taxi"],
            "constraints": {"physical_intensity": "low", "must_visit": ["鼓浪屿"]},
        },
    },
    {
        "id": "chongqing_4d_medium_mixed",
        "ugc": """重庆四日游：住重庆来福士洲际酒店，中等强度，公共交通和打车结合。\n第一天解放碑、八一路好吃街、白象居；第二天湖广会馆、洪崖洞，洪崖洞要晚上看；第三天李子坝、鹅岭二厂、磁器口；第四天南山一棵树，留半天机动。\n餐饮想吃一次火锅（瓜西西火锅）和两次重庆小面，不要把小面当成正式晚餐。山城坡多，连续爬坡不要排太满。""",
        "raw_input": "重庆四日游，中等强度，洪崖洞晚上安排，公共交通优先，洪崖洞和南山一棵树必去。",
        "notes": "解放碑、八一路好吃街、白象居、湖广会馆、洪崖洞、李子坝、鹅岭二厂、磁器口、南山一棵树、瓜西西火锅、花市豌杂面。洪崖洞要求 19:00 后。",
        "user_profile": {
            "destination": "重庆",
            "start_date": "2026-09-07",
            "days": 4,
            "hotel_name": "重庆来福士洲际酒店",
            "route_goal": "food",
            "transport_preference": ["public_transport", "taxi", "walking"],
            "constraints": {"physical_intensity": "medium", "must_visit": ["洪崖洞", "南山一棵树"]},
        },
        "expected_time_windows": {"洪崖洞": "night"},
    },
    {
        "id": "hangzhou_3d_high_nature",
        "ugc": """杭州三日游：住杭州西子湖四季酒店，高强度但希望路线按区域聚合。\n第一天西湖断桥、孤山、曲院风荷；第二天灵隐寺和中国茶叶博物馆；第三天京杭大运河拱宸桥、河坊街。\n西湖和灵隐寺必去，早上可以早起。楼外楼安排一顿正餐，知味观作为小吃即可。交通以打车为主，景区内部步行。""",
        "raw_input": "杭州三日自然人文游，高强度，西湖和灵隐寺必去，景区内步行、跨区打车。",
        "notes": "西湖断桥、孤山、曲院风荷、灵隐寺、中国茶叶博物馆、京杭大运河拱宸桥、河坊街、楼外楼、知味观。不要把西湖和运河安排在同一上午。",
        "user_profile": {
            "destination": "杭州",
            "start_date": "2026-09-14",
            "days": 3,
            "hotel_name": "杭州西子湖四季酒店",
            "route_goal": "nature",
            "transport_preference": ["taxi", "walking", "public_transport"],
            "constraints": {"physical_intensity": "high", "must_visit": ["西湖", "灵隐寺"]},
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run six realistic end-to-end acceptance scenarios.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print compact acceptance evidence instead of full itineraries.",
    )
    args = parser.parse_args()
    load_project_env()
    missing = [name for name in ("LLM_API_KEY", "AMAP_API_KEY") if not os.getenv(name)]
    if missing:
        print(json.dumps({"ok": False, "error": "missing_configuration", "missing": missing}, ensure_ascii=False))
        return 2

    with tempfile.TemporaryDirectory(prefix="trajecta-live-six-") as temp_dir:
        os.environ["DATABASE_PATH"] = os.path.join(temp_dir, "acceptance.sqlite3")
        from fastapi.testclient import TestClient
        from main import create_app

        results = []
        with TestClient(create_app()) as client:
            for scenario in SCENARIOS:
                result = run_scenario(client, scenario)
                result["ugc"] = scenario["ugc"]
                result["profile"] = scenario["user_profile"]
                results.append(result)

        summary = {
            "ok": all(result["technical_ok"] and result["product_checks_passed"] for result in results),
            "scenario_count": len(results),
            "technical_success_count": sum(1 for result in results if result["technical_ok"]),
            "product_pass_count": sum(1 for result in results if result["product_checks_passed"]),
            "results": results,
        }
        printable = _compact_summary(summary) if args.summary_only else summary
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        return 0 if summary["ok"] else 1


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": summary["ok"],
        "scenario_count": summary["scenario_count"],
        "technical_success_count": summary["technical_success_count"],
        "product_pass_count": summary["product_pass_count"],
        "results": [
            {
                "id": result["id"],
                "technical_ok": result["technical_ok"],
                "fact_status": result.get("fact_status"),
                "experience_status": result.get("experience_status"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "hotel_anchor": (result.get("hotel_anchor") or {}).get("standard_name"),
                "intent_commitment_count": len(result.get("intent_commitments") or []),
                "day_loads": [
                    {
                        "day": day.get("day"),
                        "total_outing_min": day.get("total_outing_min"),
                        "place_count": len(day.get("places") or []),
                    }
                    for day in result.get("days") or []
                ],
                "issue_types": [issue.get("type") for issue in result.get("verification_issues") or []],
                "product_checks_passed": result.get("product_checks_passed"),
                "product_check_failures": result.get("product_check_failures") or [],
                "product_advisories": result.get("product_advisories") or [],
            }
            for result in summary.get("results") or []
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
