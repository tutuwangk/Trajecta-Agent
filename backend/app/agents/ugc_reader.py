from __future__ import annotations

SYSTEM_PROMPT = """你是旅行笔记地点召回器。完整抽取用户明确想去、想吃、想喝、想逛或想购买的地点与品牌，包括景点、餐厅、饮品店、烘焙店、商店、街区和商圈。
短中文品牌、英文或中英混合品牌、包含 & / · / - 的名称、尚未指定分店的连锁品牌也必须保留；下游会负责地图确认和分店选择。不要因为名称短、像品牌或缺少“店”字而漏掉。仅排除普通商品名、形容词和没有专名的泛称。宁可保留用户明确表达出行意图的候选，也不要在本阶段过早过滤。必须输出 JSON。"""


def extract_ugc_items(notes: str, llm_client) -> list[dict]:
    try:
        payload = llm_client.json_chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"""逐句检查所有“去、吃、喝、逛、看、买、打卡、顺便、再”等出行意图，不要漏掉并列或连续动作中的短品牌。请解析以下旅行资料，输出格式：
{{"ugc_items":[{{"note_id":"note_001","raw_text":"...","summary":"...","mentioned_pois":[{{"raw_name":"...","context":"...","sentiment":"positive|neutral|negative","experience_tags":["..."],"possible_category":"...","confidence":0.0}}],"food_mentions":[],"avoid_mentions":[],"time_hints":[],"crowd_hints":[],"price_hints":[],"transport_hints":[]}}]}}

笔记：
{notes}
""",
                },
            ],
            step="extract_ugc",
        )
    except Exception:
        return []
    items = payload.get("ugc_items", payload if isinstance(payload, list) else [])
    return items if isinstance(items, list) else []
