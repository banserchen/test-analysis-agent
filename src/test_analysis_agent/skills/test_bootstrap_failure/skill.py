"""Skill implementation: pytest/pip/venv bootstrap failure diagnosis."""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

_PHASE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # pip install failures
    (
        "pip_install",
        re.compile(
            r"(?:pip install.*(?:ERROR|FAIL|Could not install)|"
            r"ERROR: (?:Could not find|No matching distribution)|"
            r"ResolutionImpossible|"
            r"subprocess-exited-with-error)",
            re.IGNORECASE,
        ),
        "Check requirements file for version conflicts or missing packages",
    ),
    # venv creation failures
    (
        "venv_creation",
        re.compile(
            r"(?:(?:virtualenv|venv|python -m venv).*(?:ERROR|FAIL|not found)|"
            r"(?:Error creating virtual ?env)|"
            r"(?:No such file or directory.*python))",
            re.IGNORECASE,
        ),
        "Ensure the correct Python version is installed and accessible",
    ),
    # pytest collection errors
    (
        "pytest_collection",
        re.compile(
            r"(?:(?:collection|import)\s+error|"
            r"E\s+(?:ModuleNotFoundError|ImportError)|"
            r"(?:no tests ran|collected 0 items / \d+ error))",
            re.IGNORECASE,
        ),
        "Fix import errors or missing dependencies in test modules",
    ),
    # pytest startup failures
    (
        "pytest_startup",
        re.compile(
            r"(?:(?:INTERNALERROR|ERROR)\s+.*pytest|"
            r"pytest:\s+error|"
            r"(?:conftest\.py|plugin).*(?:Error|Exception))",
            re.IGNORECASE,
        ),
        "Check conftest.py and pytest plugins for configuration errors",
    ),
]


class TestBootstrapFailureSkill(BaseSkill):
    """Diagnose pytest, pip, or virtualenv startup failures that prevent test execution."""

    name = "test_bootstrap_failure"
    description = "Diagnose pytest, pip, or virtualenv startup failures that prevent test execution"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        log_text = context.get("log_text", "")
        if not log_text:
            return False
        # Check if log shows signs of bootstrap issues
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
                # Extract nearby context for error_details
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
