"""UI descriptions and observable capability checks, without client-name guesses."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

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


def _labels(kind: str, recommendation: list[str], sep: str) -> str:
    return sep.join(
        f"{x['value']}{'=' if sep == '；' else ': '}{x['label']}" + ("（推荐）" if x["value"] in recommendation else "")
        for x in OPTIONS[kind]
    )


def _prompt(question: dict, analysis: str | None, timeout_seconds: float | None) -> str:
    kind = question["kind"]
    recommendation = json.loads(question["recommendation"])
    prompt = MESSAGES[kind]
    if analysis and analysis.strip():
        prompt += "\n分析：" + analysis.strip()[:600]
    prompt += "\n" + _labels(kind, recommendation, "；")
    if len(recommendation) > 1:
        prompt += "\n唯一推荐组合：" + ", ".join(recommendation)
    if timeout_seconds and timeout_seconds > 0:
        prompt += f"\n{timeout_seconds:g} 秒未选择按推荐项继续。"
    prompt += f"\nquestion_id={question['id']}"
    return prompt


def schema_for(question: dict, analysis: str | None = None, timeout_seconds: float | None = None) -> type[BaseModel]:
    kind = question["kind"]
    recommended = json.loads(question["recommendation"])
    enum = Literal.__getitem__(tuple(x["value"] for x in OPTIONS[kind]))
    is_multi = kind == "verification"
    # Keep the answer REQUIRED even though the UI receives a default. An accepted
    # empty object cannot silently confirm a recommendation through Pydantic defaults.
    field = Field(..., title=TITLES[kind], description=_prompt(question, analysis, timeout_seconds),
                  json_schema_extra={"default": recommended if is_multi else recommended[0]})
    return create_model("Choice_" + question["id"].replace("-", "_"),
                        __config__=ConfigDict(extra="forbid", strict=True, title=TITLES[kind]),
                        choice=(list[enum] if is_multi else enum, field))


def describe(question: dict, reason: str | None = None, analysis: str | None = None,
             timeout_seconds: float | None = None) -> dict:
    kind = question["kind"]
    recommendation = json.loads(question["recommendation"])
    labels = [f"{x['value']}: {x['label']}" + ("（推荐）" if x["value"] in recommendation else "") for x in OPTIONS[kind]]
    text = MESSAGES[kind]
    if analysis and analysis.strip():
        text += "\n分析：" + analysis.strip()[:600]
    text += "\n" + "\n".join(labels)
    if len(recommendation) > 1:
        text += "\n唯一推荐组合：" + ", ".join(recommendation)
    if timeout_seconds and timeout_seconds > 0:
        text += f"\n{timeout_seconds:g} 秒未选择按推荐项继续。"
    text += f"\nquestion_id={question['id']}。请明确回复；未回复、取消或工具失败均不会自动采用推荐项。"
    return {"status": "TEXT_FALLBACK_REQUIRED" if reason else "WAITING_USER",
            "question_id": question["id"], "kind": kind, "state": question["state"],
            "options": OPTIONS[kind], "recommendation": recommendation,
            "requested_schema": schema_for(question, analysis, timeout_seconds).model_json_schema(), "display_text": text,
            "fallback_reason": reason, "ui_observation": "UI_NOT_OBSERVABLE",
            "next": "Display options and END this turn. Wait for explicit user reply, then interaction_submit.",
            "destructive_operations_authorized": False}
