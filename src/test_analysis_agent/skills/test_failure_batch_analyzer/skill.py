"""技能实现：批量分析测试失败，按根因分组生成 AnalyzedIssue。

LLM 可用时：按测试文件分块后分别调用 LLM（prompt.md 模板），让 LLM 按功能模块/根因
对同一文件内的失败用例分组，返回结构化的 AnalyzedIssue 列表，agent 将其直接加入
报告的 issues 字段。每块最多 MAX_CHUNK_SIZE 个用例，避免超出 LLM 上下文窗口。

LLM 不可用时：退化为按语义规范化后的 error_message 分组的启发式方案。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from test_analysis_agent.skills.base import LLMBaseSkill

logger = logging.getLogger(__name__)

# Valid category / severity values accepted by AnalyzedIssue
_VALID_CATEGORIES = {
    "function_bug", "test_case_failure", "test_infrastructure_error",
    "configuration_error", "network_error", "timeout_error",
    "environment_error", "unknown",
}
_VALID_SEVERITIES = {"critical", "high", "medium", "low"}

# Max failures to include in a single LLM call
MAX_CHUNK_SIZE = 30

# Max sample size to pass to LLM when a group is large (full list in affected_tests)
MAX_SAMPLE_SIZE = 15


class TestFailureBatchAnalyzerSkill(LLMBaseSkill):
    """批量分析测试失败，按根因分组生成 AnalyzedIssue 列表（LLM 驱动）。"""

    name = "test_failure_batch_analyzer"
    description = "批量分析测试失败，按根因分组生成结构化的问题列表（LLM 驱动）"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        failures = context.get("test_failures", [])
        return isinstance(failures, list) and len(failures) > 0

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        failures = context.get("test_failures", [])
        if not failures:
            return {"analyzed_issues": []}

        # Step 1: Heuristic grouping by (test_file, normalized_error_pattern)
        # This is always fast and avoids duplicate issues from chunking.
        groups = _group_by_file_and_pattern(failures)

        # Step 2: For each group, call LLM once to produce a rich AnalyzedIssue.
        # Groups are already small (same error pattern), so each LLM call is quick.
        all_issues: list[dict] = []
        for (module, norm_key), group_failures in groups.items():
            if self._llm_analyzer and self._prompt_template:
                issues = self._analyze_group_with_llm(group_failures)
            else:
                issues = [self._heuristic_issue(group_failures)]
            all_issues.extend(issues)

        return {"analyzed_issues": all_issues}

    def _analyze_group_with_llm(self, failures: list[Any]) -> list[dict]:
        """Call LLM once for a single heuristic group (same file + same error pattern)."""
        # Pass a representative sample to avoid oversized prompts (keep count info)
        sample = failures[:MAX_SAMPLE_SIZE]
        all_test_names = [_get(f, "test_name", "unknown") for f in failures]
        sample_descs = []
        for f in sample:
            sample_descs.append({
                "test_name": _get(f, "test_name", "unknown"),
                "status": _get(f, "status", "failed"),
                "error_message": _truncate(_get(f, "error_message") or "N/A", 500),
                "stack_trace": _truncate(_get(f, "stack_trace") or "N/A", 500),
            })

        prompt = self.render_prompt(
            count=len(failures),
            sample_count=len(sample),
            failures=sample_descs,
        )
        result_text = self.call_llm(prompt)

        if not result_text:
            logger.warning("LLM returned no result for group analysis, using heuristic")
            return [self._heuristic_issue(failures)]

        try:
            parsed = _parse_json(result_text)
            if not isinstance(parsed, list):
                parsed = [parsed]

            issues = []
            for item in parsed:
                # Use LLM-supplied affected_tests, but fall back to all test names
                affected = item.get("affected_tests") or all_test_names
                issues.append({
                    "title": item.get("title", "测试失败组"),
                    "description": item.get("description", ""),
                    "category": _safe(item.get("category", "test_case_failure"), _VALID_CATEGORIES, "test_case_failure"),
                    "severity": _safe(item.get("severity", "medium"), _VALID_SEVERITIES, "medium"),
                    "affected_tests": affected,
                    "root_cause": item.get("root_cause"),
                    "suggestion": item.get("suggestion"),
                    "should_file_bug": bool(item.get("should_file_bug", False)),
                    "bug_summary": item.get("bug_summary"),
                })
            return issues

        except Exception as exc:
            logger.warning("Failed to parse group analysis LLM response: %s", exc)
            return [self._heuristic_issue(failures)]

    def _heuristic_issue(self, failures: list[Any]) -> dict:
        """Build a single heuristic AnalyzedIssue from a failure group."""
        raw_error = _get(failures[0], "error_message") or "unknown error"
        affected = [_get(f, "test_name", "unknown") for f in failures]
        return {
            "title": f"测试失败：{raw_error[:60]}",
            "description": f"共 {len(failures)} 个用例失败，错误：{raw_error[:200]}",
            "category": "test_case_failure",
            "severity": "medium",
            "affected_tests": affected,
            "root_cause": None,
            "suggestion": None,
            "should_file_bug": False,
            "bug_summary": None,
        }

    def _heuristic_fallback(self, failures: list[Any]) -> list[dict]:
        """Fallback: group by normalized error, return heuristic issues."""
        groups: dict[str, list[Any]] = {}
        for f in failures:
            key = _normalize_error(_get(f, "error_message") or "")
            groups.setdefault(key, []).append(f)
        return [self._heuristic_issue(g) for g in groups.values()]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _normalize_error(error_msg: str) -> str:
    """Normalize error messages to enable semantic grouping in heuristic mode.

    Strips assertion value details (everything after the first newline) and
    replaces numeric values with {N} so that failures with the same pattern
    but different values (e.g. "任务 2 超时" vs "任务 14 超时") are merged.
    """
    if not error_msg:
        return "__no_error__"
    # Take first line only — removes pytest assertion detail lines
    first_line = error_msg.split("\n")[0].strip()
    # Replace numbers (integers and floats) with placeholder
    normalized = re.sub(r"\b\d+(\.\d+)?\b", "{N}", first_line)
    return normalized or first_line


def _group_by_file_and_pattern(failures: list[Any]) -> dict[tuple, list[Any]]:
    """Group failures by (test_file_module, normalized_error_pattern).

    This two-key grouping ensures:
    - Failures from different test files are always separate groups
    - Failures from the same file with the same root cause are merged,
      regardless of numeric differences in the error message
    """
    groups: dict[tuple, list[Any]] = {}
    for f in failures:
        test_name = _get(f, "test_name", "") or ""
        module = test_name.rsplit("#", 1)[0] if "#" in test_name else test_name
        error = _get(f, "error_message") or ""
        norm = _normalize_error(error)
        key = (module, norm)
        groups.setdefault(key, []).append(f)
    return groups


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _truncate(text: Any, max_chars: int) -> str:
    if not text:
        return ""
    text = str(text)
    return text if len(text) <= max_chars else text[:max_chars] + "\n... [truncated]"


def _safe(value: str, valid: set[str], default: str) -> str:
    return value if value in valid else default


def _parse_json(text: str) -> Any:
    import json
    import re
    clean = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    for start_ch in ('{', '['):
        idx = clean.find(start_ch)
        if idx >= 0:
            try:
                return json.loads(clean[idx:])
            except json.JSONDecodeError:
                pass
    return json.loads(clean)
