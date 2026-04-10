"""技能实现：pytest/pip/venv 启动失败诊断。"""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

_PHASE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # pip 安装失败
    (
        "pip_install",
        re.compile(
            r"(?:pip install.*(?:ERROR|FAIL|Could not install)|"
            r"ERROR: (?:Could not find|No matching distribution)|"
            r"ResolutionImpossible|"
            r"subprocess-exited-with-error)",
            re.IGNORECASE,
        ),
        "检查 requirements 文件中的版本冲突或缺失包",
    ),
    # venv 创建失败
    (
        "venv_creation",
        re.compile(
            r"(?:(?:virtualenv|venv|python -m venv).*(?:ERROR|FAIL|not found)|"
            r"(?:Error creating virtual ?env)|"
            r"(?:No such file or directory.*python))",
            re.IGNORECASE,
        ),
        "确认正确的 Python 版本已安装且路径可访问",
    ),
    # pytest 收集错误
    (
        "pytest_collection",
        re.compile(
            r"(?:(?:collection|import)\s+error|"
            r"E\s+(?:ModuleNotFoundError|ImportError)|"
            r"(?:no tests ran|collected 0 items / \d+ error))",
            re.IGNORECASE,
        ),
        "修复测试模块中的导入错误或缺失依赖",
    ),
    # pytest 启动失败
    (
        "pytest_startup",
        re.compile(
            r"(?:(?:INTERNALERROR|ERROR)\s+.*pytest|"
            r"pytest:\s+error|"
            r"(?:conftest\.py|plugin).*(?:Error|Exception))",
            re.IGNORECASE,
        ),
        "检查 conftest.py 和 pytest 插件的配置错误",
    ),
]


class TestBootstrapFailureSkill(BaseSkill):
    """诊断 pytest、pip 或 virtualenv 启动失败，导致测试无法执行的问题。"""

    name = "test_bootstrap_failure"
    description = "诊断 pytest、pip 或 virtualenv 启动失败，导致测试无法执行"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        log_text = context.get("log_text", "")
        if not log_text:
            return False
        # 检查日志是否存在启动问题的迹象
        return bool(re.search(
            r"(?:pip install|virtualenv|venv|pytest.*(?:ERROR|INTERNALERROR)|"
            r"collected 0 items|no tests ran)",
            log_text,
            re.IGNORECASE,
        ))

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        log_text: str = context.get("log_text", "")

        for phase, pattern, suggestion in _PHASE_PATTERNS:
            match = pattern.search(log_text)
            if match:
                # 提取错误附近的上下文作为 error_details
                start = max(0, match.start() - 200)
                end = min(len(log_text), match.end() + 200)
                error_details = log_text[start:end].strip()

                return {
                    "is_bootstrap_failure": True,
                    "bootstrap_phase": phase,
                    "error_details": error_details,
                    "suggestion": suggestion,
                }

        return {
            "is_bootstrap_failure": False,
            "bootstrap_phase": "unknown",
            "error_details": "",
            "suggestion": "",
        }
