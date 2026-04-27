"""技能实现：流水线阶段失败 LLM 深度分析。

对流水线中最主要的失败阶段调用 LLM 进行根因分析，
作为确定性规则（触发下游 job 分析）的补充视角。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from test_analysis_agent.skills.base import LLMBaseSkill

logger = logging.getLogger(__name__)


class StageFailureAnalyzerSkill(LLMBaseSkill):
    name = "stage_failure_analyzer"
    description = "使用 LLM 对流水线阶段失败进行深度根因分析，生成结构化问题报告。"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        return (
            self._llm_analyzer is not None
            and context.get("primary_stage") is not None
        )

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        primary_stage = context["primary_stage"]

        triggered_jobs = getattr(primary_stage, "triggered_jobs", []) or []
        triggered_section = ""
        if triggered_jobs:
            jobs_text = "\n".join(
                f"- {j.job_name} #{j.build_number}: {j.status}" for j in triggered_jobs
            )
            triggered_section = f"触发的下游任务:\n{jobs_text}"

        stage_label = (
            getattr(primary_stage, "stage_name", "")
            or getattr(primary_stage.stage, "value", "unknown")
        )

        prompt = self.render_prompt(
            stage=stage_label,
            status=getattr(primary_stage, "status", "fail"),
            error_summary=_truncate(getattr(primary_stage, "error_summary", None) or "N/A", 3000),
            log_excerpt=_truncate(getattr(primary_stage, "log_excerpt", None) or "N/A", 3000),
            triggered_jobs_section=triggered_section,
        )

        result_text = self.call_llm(prompt)
        if not result_text:
            return {}

        try:
            parsed = _parse_json(result_text)
            return {"llm_stage_issue": parsed}
        except Exception as exc:
            logger.warning("Failed to parse stage failure analysis response: %s", exc)
            return {}


def _truncate(text: str | None, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines)
    return json.loads(cleaned)
