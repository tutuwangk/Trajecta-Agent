from app.agents import planner, reviser
from app.agents.verifier import review_soft_quality


def test_json_chat_prompts_include_deepseek_json_keyword():
    context = planner.compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "IFS",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "final_decision": "include",
            }
        ],
        [],
    )
    copy_context = planner.build_copy_context(
        context,
        {"destination": "成都", "days": [{"day": 1, "items": [], "removed_pois": []}], "global_risks": []},
    )

    prompt_sets = {
        "semantic": planner._semantic_messages(context),
        "blueprint": planner._blueprint_messages(context),
        "replan_blueprint": planner._replan_blueprint_messages(context, {"days": []}, {"issues": []}),
        "copy": reviser._copy_messages(copy_context),
        "soft_review": _soft_review_messages(),
    }

    missing_json = [name for name, messages in prompt_sets.items() if "json" not in _message_text(messages).lower()]

    assert missing_json == []


def _soft_review_messages():
    class CaptureLLM:
        def __init__(self):
            self.messages = None

        def json_chat(self, messages, step, temperature=0.2):
            self.messages = messages
            return {"issues": []}

    llm = CaptureLLM()
    review_soft_quality(
        {"destination": "成都", "days": [{"day": 1, "items": []}]},
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [],
        [],
        llm_client=llm,
    )
    return llm.messages


def _message_text(messages):
    return "\n".join(str(message.get("content", "")) for message in messages)
