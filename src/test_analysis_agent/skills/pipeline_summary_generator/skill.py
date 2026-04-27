"""技能实现：流水线分析总结与 Bug 推荐生成。

接收已组装的 issues 列表和任务元信息，调用 LLM 生成：
1. 执行摘要（executive summary）
2. 结构化 Bug 归档推荐列表
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from test_analysis_agent.models.schemas import (
    BugRecommendation,
    FailureCategory,
    PipelineStage,
    Severity,
)
from test_analysis_agent.skills.base import LLMBaseSkill

logger = logging.getLogger(__name__)


class PipelineSummaryGeneratorSkill(LLMBaseSkill):
    name = "pipeline_summary_generator"
    description = "基于完整分析结果生成执行摘要和结构化 Bug 归档推荐列表。"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        return (
            self._llm_analyzer is not None
            and "job_name" in context
            and "issues" in context
        )

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        issues = context.get("issues", [])
        issues_text = "\n".join(
            f"- [{i.severity.value.upper()}] {i.title}: {i.description[:200]}"
            for i in issues
        )

        failure_stage = context.get("failure_stage")
        failure_stage_name = context.get("failure_stage_name", "")
        display_stage = failure_stage_name or (failure_stage.value if failure_stage else "unknown")

        prompt = self.render_prompt(
            job_name=context.get("job_name", ""),
            build_number=context.get("build_number", 0),
            failure_stage=display_stage,
            issues_text=issues_text or "No specific issues identified",
            total_tests=context.get("total_tests", 0),
            passed_tests=context.get("passed_tests", 0),
            failed_tests=context.get("failed_tests", 0),
            broken_tests=context.get("broken_tests", 0),
        )

        result_text = self.call_llm(prompt)
        if not result_text:
            return {
                "summary": "Analysis completed but summary generation failed.",
                "bug_recommendations": [],
            }

        try:
            parsed = _parse_json(result_text)
            raw_recs = parsed.get("bug_recommendations", []) or []
            bug_recs = _coerce_bug_recommendations(raw_recs, issues, failure_stage)
            return {
                "summary": parsed.get("summary", ""),
                "bug_recommendations": bug_recs,
            }
        except Exception as exc:
            logger.warning("Failed to parse summary response: %s", exc)
            return {
                "summary": "Analysis completed but summary generation failed.",
                "bug_recommendations": [],
            }


# ------------------------------------------------------------------
# Helpers (migrated from llm_analyzer.py)
# ------------------------------------------------------------------

def _truncate(text: Optional[str], max_chars: int) -> str:
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


def _coerce_bug_recommendations(
    raw: list,
    issues: list,
    failure_stage: Optional[PipelineStage],
) -> list[BugRecommendation]:
    """Convert raw LLM output into BugRecommendation objects."""
    recs: list[BugRecommendation] = []
    for item in raw:
        if isinstance(item, dict):
            summary = (item.get("summary") or item.get("title") or "").strip()
            detail = (item.get("detail_description") or item.get("description") or "").strip()
            if not summary and not detail:
                continue
            if not summary:
                summary = detail.splitlines()[0][:120]
            if not detail:
                detail = _fallback_detail(summary, issues, failure_stage)
            try:
                severity = Severity(item["severity"]) if item.get("severity") else None
            except ValueError:
                severity = None
            try:
                category = FailureCategory(item["category"]) if item.get("category") else None
            except ValueError:
                category = None
            try:
                stage = PipelineStage(item["affected_stage"]) if item.get("affected_stage") else None
            except ValueError:
                stage = None
            affected_jobs = item.get("affected_jobs") or []
            if not isinstance(affected_jobs, list):
                affected_jobs = [str(affected_jobs)]
            recs.append(
                BugRecommendation(
                    summary=summary,
                    detail_description=detail,
                    severity=severity,
                    category=category,
                    affected_stage=stage,
                    affected_jobs=[str(j) for j in affected_jobs],
                )
            )
        elif isinstance(item, str) and item.strip():
            recs.append(
                BugRecommendation(
                    summary=item.strip().splitlines()[0][:120],
                    detail_description=_fallback_detail(item.strip(), issues, failure_stage),
                    affected_stage=failure_stage,
                )
            )
    return recs


def _fallback_detail(text: str, issues: list, failure_stage: Optional[PipelineStage]) -> str:
    lines = [text]
    if issues:
        lines.append("")
        lines.append("相关问题证据：")
        for issue in issues[:3]:
            lines.append(
                f"- [{issue.severity.value}] {issue.title} (stage={issue.stage.value}): "
                f"{(issue.root_cause or issue.description or '')[:200]}"
            )
    lines.append("")
    stage_val = failure_stage.value if failure_stage else "unknown"
    lines.append(f"受影响阶段：{stage_val}。建议按照上述根因与修复建议推进并在完成后回归验证。")
    return "\n".join(lines)
