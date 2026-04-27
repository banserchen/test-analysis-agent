"""技能实现：Allure 失败用例深度分析（LLM驱动）。

通过 LLM 对失败用例进行分类归因和 AI 分析，并在 post_process 阶段将
ai_analysis 和 failure_category 写回报告中的 test_failures 列表。
当无 LLM 可用时，退化为基于规则的启发式分类。

测试脚本源码由 agent._analyze_test_failures() 预先填充到 failure.related_code，
本技能在构建 LLM 提示时直接使用，以支持对 broken 用例的深度根因分析。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from test_analysis_agent.models.schemas import FailureCategory
from test_analysis_agent.skills.base import LLMBaseSkill

logger = logging.getLogger(__name__)

# 启发式分类：先对 error_message 做高置信度匹配，再对完整文本匹配。
# 规则按优先级排列，越早越优先。

# 仅匹配 error_message 的高优先级规则（不受 stack_trace 中无关词汇干扰）
_ERROR_MSG_RULES: list[tuple[str, re.Pattern[str]]] = [
    # 测试脚本编程错误（变量未初始化、NameError 等）
    ("test_case_failure", re.compile(
        r"(?:UnboundLocalError|NameError)",
        re.IGNORECASE,
    )),
    # 产品功能断言失败
    ("function_bug", re.compile(
        r"(?:AssertionError)",
        re.IGNORECASE,
    )),
]

# 对完整文本（error_message + stack_trace）匹配的兜底规则
_HEURISTIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("test_infrastructure_error", re.compile(
        r"(?:docker|container|slave|k8s|kubernetes|"
        r"java\.net\.SocketException|java\.io\.IOException.*Connection)",
        re.IGNORECASE,
    )),
    ("environment_error", re.compile(
        r"(?:env\s+var|environment|missing.{0,20}config|"
        r"FileNotFoundError|No such file|not found.{0,20}path|"
        r"Connection refused|ECONNREFUSED)",
        re.IGNORECASE,
    )),
    ("test_case_failure", re.compile(
        r"(?:fixture.{0,30}error|setup\s+error|teardown\s+error|xfail|skip)",
        re.IGNORECASE,
    )),
    ("function_bug", re.compile(
        r"(?:HTTP\s+[45]\d{2}|status.{0,10}(?:500|502|503)|"
        r"NullPointerException|TypeError|AttributeError|"
        r"Unexpected\s+response|unexpected\s+result)",
        re.IGNORECASE,
    )),
]

# Valid FailureCategory values (kept in sync with schemas.FailureCategory)
_VALID_CATEGORIES = {
    "environment_error", "dependency_error", "deployment_error",
    "configuration_error", "network_error", "permission_error",
    "test_startup_failure", "test_case_failure", "test_infrastructure_error",
    "timeout_error", "resource_error", "function_bug", "unknown",
}

class AllureCaseFailureClassifierSkill(LLMBaseSkill):
    """对 Allure 报告中的失败用例进行深度分析（LLM驱动）。

    LLM 可用时：按 error_message 分组后调用 LLM（prompt.md），
    获取每个用例的 ai_analysis、failure_class、failure_category。
    LLM 不可用时：退化为启发式规则分类。
    """

    name = "allure_case_failure_classifier"
    description = "对 Allure 报告中的失败用例进行 LLM 驱动的深度分析与分类归因"
    version = "2.0.0"

    def __init__(self) -> None:
        super().__init__()
        self._last_result: dict[str, Any] = {}

    def can_handle(self, context: dict[str, Any]) -> bool:
        failures = context.get("test_failures", [])
        return isinstance(failures, list) and len(failures) > 0

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        failures = context.get("test_failures", [])

        # Always use heuristic — the batch_analyzer handles deep LLM analysis per group.
        # Running LLM here on all 100+ failures causes timeouts and duplicates work.
        result = self._execute_heuristic(failures)

        # Store for post_process to write ai_analysis back to report
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # LLM path
    # ------------------------------------------------------------------

    def _execute_with_llm(self, failures: list[Any]) -> dict[str, Any]:
        """Group failures by error_message, call LLM once per group."""
        # Group by error_message
        groups: dict[str, list[Any]] = {}
        for f in failures:
            key = (_get(f, "error_message") or "").strip() or f"__no_error_{id(f)}__"
            groups.setdefault(key, []).append(f)

        # Build group descriptors for the prompt
        failure_groups = []
        for group_failures in groups.values():
            rep = group_failures[0]
            status = _get(rep, "status", "failed")
            # related_code is pre-populated by agent._analyze_test_failures()
            # when request.test_repo_path is configured
            source_code = _get(rep, "related_code") or ""

            failure_groups.append({
                "count": len(group_failures),
                "test_names": [_get(f, "test_name", "unknown") for f in group_failures],
                "status": status,
                "error_message": _truncate(_get(rep, "error_message") or "N/A", 1000),
                "stack_trace": _truncate(_get(rep, "stack_trace") or "N/A", 1500),
                "source_code": _truncate(source_code, 3000),
            })

        prompt = self.render_prompt(failure_groups=failure_groups, total=len(failures))
        result_text = self.call_llm(prompt)

        if not result_text:
            logger.warning("LLM returned no result for allure classifier, falling back to heuristic")
            return self._execute_heuristic(failures)

        try:
            parsed = _parse_json(result_text)
            classifications_raw = parsed.get("classifications", [])
            # Build lookup by test_name
            llm_by_name: dict[str, dict] = {
                item.get("test_name", ""): item for item in classifications_raw
            }

            classifications: list[dict[str, Any]] = []
            by_category: dict[str, int] = {}
            for f in failures:
                test_name = _get(f, "test_name", "unknown")
                llm_item = llm_by_name.get(test_name, {})
                failure_category = llm_item.get("failure_category", "unknown")
                if failure_category not in _VALID_CATEGORIES:
                    failure_category = "unknown"
                ai_analysis = llm_item.get("ai_analysis", "")
                reason = llm_item.get("reason", "")
                confidence = float(llm_item.get("confidence", 0.7))

                classifications.append({
                    "test_name": test_name,
                    "failure_category": failure_category,
                    "ai_analysis": ai_analysis,
                    "reason": reason,
                    "confidence": round(confidence, 2),
                })
                by_category[failure_category] = by_category.get(failure_category, 0) + 1

            return {
                "classifications": classifications,
                "summary": {"total": len(classifications), "by_category": by_category},
            }
        except Exception as exc:
            logger.warning("Failed to parse LLM classifier response: %s", exc)
            return self._execute_heuristic(failures)

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _execute_heuristic(self, failures: list[Any]) -> dict[str, Any]:
        """Rule-based classification when LLM is unavailable."""
        classifications: list[dict[str, Any]] = []
        by_category: dict[str, int] = {}

        for failure in failures:
            test_name = _get(failure, "test_name", "unknown")
            error_msg = _get(failure, "error_message", "") or ""
            stack_trace = _get(failure, "stack_trace", "") or ""
            categories = _get(failure, "categories", [])

            combined = f"{error_msg}\n{stack_trace}\n{' '.join(categories) if isinstance(categories, list) else ''}"

            failure_category = "unknown"
            confidence = 0.3
            reason = "无法从现有信息中判断失败类别"

            # Stage 1: match error_message only (high confidence, unaffected by stack_trace noise)
            for cat, pattern in _ERROR_MSG_RULES:
                if pattern.search(error_msg):
                    failure_category = cat
                    confidence = 0.8
                    reason = f"error_message 中匹配到 {cat} 模式"
                    break

            # Stage 2: match full combined text (lower priority fallback)
            if failure_category == "unknown":
                for cat, pattern in _HEURISTIC_RULES:
                    if pattern.search(combined):
                        failure_category = cat
                        confidence = 0.65
                        reason = f"在错误信息/栈追踪中匹配到 {cat} 模式"
                        break

            # Stage 3: Allure category labels
            if failure_category == "unknown" and isinstance(categories, list):
                for cat in categories:
                    cat_lower = cat.lower() if isinstance(cat, str) else ""
                    if "product" in cat_lower or "bug" in cat_lower:
                        failure_category = "function_bug"
                        confidence = 0.8
                        reason = f"Allure 分类标签表明为产品缺陷：{cat}"
                        break
                    if "flaky" in cat_lower or "intermittent" in cat_lower:
                        failure_category = "test_case_failure"
                        confidence = 0.75
                        reason = f"Allure 分类标签表明为不稳定测试：{cat}"
                        break

            classifications.append({
                "test_name": test_name,
                "failure_category": failure_category,
                "ai_analysis": "",
                "reason": reason,
                "confidence": round(confidence, 2),
            })
            by_category[failure_category] = by_category.get(failure_category, 0) + 1

        return {
            "classifications": classifications,
            "summary": {"total": len(classifications), "by_category": by_category},
        }

    def post_process(self, report: Any) -> Any:
        """Write ai_analysis and failure_category back to report.test_failures."""
        skill_result = getattr(self, "_last_result", None)
        if not skill_result:
            return report

        classifications_by_name = {
            item["test_name"]: item
            for item in skill_result.get("classifications", [])
        }

        if not hasattr(report, "test_failures"):
            return report

        for failure in report.test_failures:
            test_name = getattr(failure, "test_name", None)
            if not test_name:
                continue
            item = classifications_by_name.get(test_name)
            if not item:
                continue
            if item.get("ai_analysis") and not getattr(failure, "ai_analysis", None):
                failure.ai_analysis = item["ai_analysis"]
            if not getattr(failure, "failure_category", None) or failure.failure_category == FailureCategory.UNKNOWN:
                raw = item.get("failure_category", "unknown")
                try:
                    failure.failure_category = FailureCategory(str(raw))
                except ValueError:
                    failure.failure_category = FailureCategory.UNKNOWN

        return report


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _truncate(text: Any, max_chars: int) -> str:
    if not text:
        return ""
    text = str(text)
    return text if len(text) <= max_chars else text[:max_chars] + "\n... [truncated]"


def _parse_json(text: str) -> Any:
    """Extract and parse JSON from LLM response text."""
    import json
    import re as _re
    # Strip markdown code fences
    clean = _re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    # Find first JSON object or array
    for start_ch, end_ch in [('{', '}'), ('[', ']')]:
        idx = clean.find(start_ch)
        if idx >= 0:
            try:
                return json.loads(clean[idx:])
            except json.JSONDecodeError:
                pass
    return json.loads(clean)
