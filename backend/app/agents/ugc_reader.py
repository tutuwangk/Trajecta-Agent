from __future__ import annotations

SYSTEM_PROMPT = """你是旅行笔记解析器。只抽取真实旅行地点、餐厅、景点、街区、商圈、拍照点。
排除普通商品名、形容词、泛称和无法定位的描述。必须输出 JSON。"""


def extract_ugc_items(notes: str, llm_client) -> list[dict]:
    payload = llm_client.json_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""请解析以下小红书笔记，输出格式：
{{"ugc_items":[{{"note_id":"note_001","raw_text":"...","summary":"...","mentioned_pois":[{{"raw_name":"...","context":"...","sentiment":"positive|neutral|negative","experience_tags":["..."],"possible_category":"...","confidence":0.0}}],"food_mentions":[],"avoid_mentions":[],"time_hints":[],"crowd_hints":[],"price_hints":[],"transport_hints":[]}}]}}

笔记：
{notes}
""",
            },
        ],
        step="extract_ugc",
    )
    items = payload.get("ugc_items", payload if isinstance(payload, list) else [])
    return items if isinstance(items, list) else []
