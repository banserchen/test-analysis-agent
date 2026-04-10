"""技能实现：Allure 失败用例分类归因。"""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

# 启发式关键词 → 失败类别映射
_CLASS_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("infrastructure", re.compile(
        r"(?:docker|container|agent|slave|node|k8s|kubernetes|jenkins|"
        r"java\.net\.SocketException|java\.io\.IOException.*Connection)",
        re.IGNORECASE,
    )),
    ("environment", re.compile(
        r"(?:env\s+var|environment|missing.{0,20}config|"
        r"FileNotFoundError|No such file|not found.{0,20}path|"
        r"Connection refused|ECONNREFUSED)",
        re.IGNORECASE,
    )),
    ("test_bug", re.compile(
        r"(?:AssertionError|assert\s+.*==|fixture.{0,30}error|"
        r"setup\s+error|teardown\s+error|xfail|skip)",
        re.IGNORECASE,
    )),
    ("product_bug", re.compile(
        r"(?:HTTP\s+[45]\d{2}|status.{0,10}(?:500|502|503)|"
        r"NullPointerException|TypeError|AttributeError|"
        r"Unexpected\s+response|unexpected\s+result)",
        re.IGNORECASE,
    )),
]


class AllureCaseFailureClassifierSkill(BaseSkill):
    """对 Allure 报告中的失败用例进行分类归因。"""

    name = "allure_case_failure_classifier"
    description = "对 Allure 报告中的失败用例进行分类归因"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        failures = context.get("test_failures", [])
        return isinstance(failures, list) and len(failures) > 0

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        failures = context.get("test_failures", [])
        classifications: list[dict[str, Any]] = []
        by_class: dict[str, int] = {}

        for failure in failures:
            # 支持 dict 和 object 两种访问方式
            test_name = _get(failure, "test_name", "unknown")
            error_msg = _get(failure, "error_message", "")
            stack_trace = _get(failure, "stack_trace", "")
            categories = _get(failure, "categories", [])

            combined = f"{error_msg}\n{stack_trace}\n{' '.join(categories) if isinstance(categories, list) else ''}"

            failure_class = "unknown"
            confidence = 0.3
            reason = "无法从现有信息中判断失败类别"

            for cls, pattern in _CLASS_RULES:
                if pattern.search(combined):
                    failure_class = cls
                    confidence = 0.7
                    reason = f"在错误信息/栈追踪中匹配到 {cls} 模式"
                    break

            # 检查 Allure 分类标签中的提示
            if isinstance(categories, list):
                for cat in categories:
                    cat_lower = cat.lower() if isinstance(cat, str) else ""
                    if "product" in cat_lower or "bug" in cat_lower:
                        failure_class = "product_bug"
                        confidence = 0.8
                        reason = f"Allure 分类标签表明为产品缺陷：{cat}"
                        break
                    if "flaky" in cat_lower or "intermittent" in cat_lower:
                        failure_class = "flaky"
                        confidence = 0.75
                        reason = f"Allure 分类标签表明为不稳定测试：{cat}"
                        break

            classifications.append({
                "test_name": test_name,
                "failure_class": failure_class,
                "reason": reason,
                "confidence": round(confidence, 2),
            })
            by_class[failure_class] = by_class.get(failure_class, 0) + 1

        return {
            "classifications": classifications,
            "summary": {
                "total": len(classifications),
                "by_class": by_class,
            },
        }


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """从 dict 或对象属性中获取值。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
