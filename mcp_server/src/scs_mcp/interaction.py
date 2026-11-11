"""UI descriptions and observable capability checks, without client-name guesses."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

from .state import OPTIONS

MESSAGES = {
    "mode": "请选择本对话执行模式。唯一推荐项只是界面默认值，须明确提交。",
    "verification": "请选择验证范围（可多选）。不做测试/只记录方法各自与其他选项互斥；破坏性操作另行确认。",
    "codex_review": "收尾是否使用已注册的 codex-review 做只读独立审查？",
}
TITLES = {
    "mode": "请选择本对话执行模式",
    "verification": "请选择验证范围",
    "codex_review": "收尾是否使用 codex-review",
}


def supports_form(capabilities) -> bool:
    elicitation = capabilities.elicitation if capabilities is not None else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def _ordered(kind: str, recommendation: list[str]) -> list[dict]:
    """Recommended options first so the form itself leads with the recommendation."""
    return ([x for x in OPTIONS[kind] if x["value"] in recommendation]
            + [x for x in OPTIONS[kind] if x["value"] not in recommendation])


def _scan_reason(analysis: str | None) -> str:
    if not analysis or not analysis.strip():
        return ""
    return analysis.strip().splitlines()[0][:150]


def _display(option: dict, recommendation: list[str], analysis: str | None = None) -> str:
    mark = "（推荐）" if option["value"] in recommendation else ""
    detail = f"：{option['desc']}" if option.get("desc") else ""
    reason = _scan_reason(analysis)
    if reason and option["value"] in recommendation:
        detail += "｜" + reason
    return f"{option['value']} — {option['label']}{mark}{detail}"


def choice_code(kind: str, value):
    """Map a form label or code back to option codes."""
    if isinstance(value, list):
        return [choice_code(kind, item) for item in value]
    for option in OPTIONS[kind]:
        if value == option["value"] or value == option["label"] or (isinstance(value, str) and value.startswith(option["value"] + " — ")):
            return option["value"]
    return value


def _prompt(question: dict, analysis: str | None, timeout_seconds: float | None) -> str:
    kind = question["kind"]
    recommendation = json.loads(question["recommendation"])
    prompt = MESSAGES[kind]
    recommended = [x for x in OPTIONS[kind] if x["value"] in recommendation]
    if recommended:
        prompt += "\n推荐：" + "、".join(f"{x['label']}（{x['value']}）" for x in recommended)
    if analysis and analysis.strip():
        prompt += "\n" + analysis.strip()[:600]
    if len(recommendation) > 1:
        prompt += "\n唯一推荐组合：" + ", ".join(recommendation)
    if timeout_seconds and timeout_seconds > 0:
        prompt += f"\n{timeout_seconds:g} 秒未选择按推荐项继续。"
    prompt += f"\nquestion_id={question['id']}"
    return prompt


def schema_for(question: dict, analysis: str | None = None, timeout_seconds: float | None = None) -> type[BaseModel]:
    kind = question["kind"]
    recommended = json.loads(question["recommendation"])
    ordered = _ordered(kind, recommended)
    is_multi = kind == "verification"
    enum = Literal.__getitem__(tuple(_display(x, recommended, analysis) for x in ordered))
    default = (
        [_display(x, recommended, analysis) for x in ordered if x["value"] in recommended]
        if is_multi
        else _display(next(x for x in ordered if x["value"] == recommended[0]), recommended, analysis)
    )

    def alias_codes(value):
        def one(item):
            if isinstance(item, str):
                for option in OPTIONS[kind]:
                    if item == option["value"]:
                        return _display(option, recommended, analysis)
            return item
        return [one(item) for item in value] if isinstance(value, list) else one(value)

    # Keep the answer REQUIRED even though the UI receives a default. An accepted
    # empty object cannot silently confirm a recommendation through Pydantic defaults.
    field = Field(..., title=TITLES[kind], description=_prompt(question, analysis, timeout_seconds),
                  json_schema_extra={"default": default})
    return create_model("Choice_" + question["id"].replace("-", "_"),
                        __config__=ConfigDict(extra="forbid", strict=True, title=TITLES[kind]),
                        __validators__={"alias_codes": field_validator("choice", mode="before")(alias_codes)},
                        choice=(list[enum] if is_multi else enum, field))


def describe(question: dict, reason: str | None = None, analysis: str | None = None,
             timeout_seconds: float | None = None) -> dict:
    kind = question["kind"]
    recommendation = json.loads(question["recommendation"])
    ordered = _ordered(kind, recommendation)
    recommended = [x for x in OPTIONS[kind] if x["value"] in recommendation]
    labels = [
        f"{x['value']}: {x['label']}" + ("（推荐）" if x["value"] in recommendation else "")
        + (f" — {x['desc']}" if x.get("desc") else "")
        + (f"｜{_scan_reason(analysis)}" if x["value"] in recommendation and _scan_reason(analysis) else "")
        for x in ordered
    ]
    text = MESSAGES[kind]
    if recommended:
        text += "\n推荐：" + "、".join(f"{x['label']}（{x['value']}）" for x in recommended)
    if analysis and analysis.strip():
        text += "\n" + analysis.strip()[:600]
    text += "\n" + "\n".join(labels)
    if len(recommendation) > 1:
        text += "\n唯一推荐组合：" + ", ".join(recommendation)
    if timeout_seconds and timeout_seconds > 0:
        text += f"\n{timeout_seconds:g} 秒未选择按推荐项继续。"
    text += f"\nquestion_id={question['id']}。请明确回复；未回复、取消或工具失败均不会自动采用推荐项。"
    return {"status": "TEXT_FALLBACK_REQUIRED" if reason else "WAITING_USER",
            "question_id": question["id"], "kind": kind, "state": question["state"],
            "options": ordered, "recommendation": recommendation,
            "requested_schema": schema_for(question, analysis, timeout_seconds).model_json_schema(), "display_text": text,
            "fallback_reason": reason, "ui_observation": "UI_NOT_OBSERVABLE",
            "next": "Display options and END this turn. Wait for explicit user reply, then interaction_submit.",
            "destructive_operations_authorized": False}
