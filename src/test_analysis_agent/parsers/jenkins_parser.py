"""Jenkins log fetcher and parser.

Fetches build logs from Jenkins and identifies pipeline stages, errors,
and triggered downstream jobs.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import jenkins

from test_analysis_agent.models.schemas import (
    PipelineStage,
    PipelineStageResult,
    TriggeredJobInfo,
)

logger = logging.getLogger(__name__)

# Patterns for identifying pipeline stages in Jenkins logs
_STAGE_PATTERNS: dict[PipelineStage, list[re.Pattern[str]]] = {
    PipelineStage.PREPARATION: [
        re.compile(r"\[Pipeline\]\s*\{\s*\((?:准备|Preparation|Setup|Environment|Init)", re.IGNORECASE),
        re.compile(r"(?:stage|阶段).*?(?:准备|preparation|setup|environment|init)", re.IGNORECASE),
        re.compile(r"(?:checkout|clone|pull|fetch)\s+(?:scm|git|repo)", re.IGNORECASE),
        re.compile(r"Installing\s+dependencies", re.IGNORECASE),
    ],
    PipelineStage.DEPLOYMENT: [
        re.compile(r"\[Pipeline\]\s*\{\s*\((?:部署|Deploy|Update|Rollout)", re.IGNORECASE),
        re.compile(r"(?:stage|阶段).*?(?:部署|deploy|update|rollout|release)", re.IGNORECASE),
        re.compile(r"(?:kubectl|helm|docker|ansible)\s+(?:apply|deploy|upgrade|push|run)", re.IGNORECASE),
    ],
    PipelineStage.TESTING: [
        re.compile(r"\[Pipeline\]\s*\{\s*\((?:测试|Test|QA|Verify|Validation)", re.IGNORECASE),
        re.compile(r"(?:stage|阶段).*?(?:测试|test|qa|verify|validation)", re.IGNORECASE),
        re.compile(r"(?:pytest|jest|mvn\s+test|gradle\s+test|robot|allure)", re.IGNORECASE),
    ],
}

_ERROR_PATTERNS = [
    re.compile(r"(?:ERROR|FATAL|FAILURE|FAILED|Exception|Error:)", re.IGNORECASE),
    re.compile(r"(?:Build failed|exit code [1-9]|non-zero exit|command not found)", re.IGNORECASE),
    re.compile(r"(?:Connection refused|timeout|timed out|unreachable)", re.IGNORECASE),
    re.compile(r"(?:Permission denied|Access denied|Unauthorized|403|401)", re.IGNORECASE),
    re.compile(r"(?:No space left|Out of memory|OOM|Cannot allocate)", re.IGNORECASE),
]

_TRIGGERED_JOB_PATTERN = re.compile(
    r"(?:Triggering|Starting|Building)\s+(?:a new build of\s+)?(?:job\s+)?['\"]?([^\s'\"]+)['\"]?"
    r"[^#\n]{0,200}#(\d+)",
    re.IGNORECASE,
)

_TRIGGERED_JOB_RESULT_PATTERN = re.compile(
    r"(?:job\s+)?['\"]?([^\s'\"]{1,200})['\"]?\s+#(\d+)\s+completed[^\n]{0,100}(SUCCESS|FAILURE|UNSTABLE|ABORTED)",
    re.IGNORECASE,
)


class JenkinsParser:
    """Fetches and parses Jenkins build logs."""

    def __init__(self, url: str, username: str, password: str):
        self._server = jenkins.Jenkins(url, username=username, password=password)
        self._url = url.rstrip("/")

    def get_build_log(self, job_name: str, build_number: int) -> str:
        """Fetch the console output for a build."""
        try:
            return self._server.get_build_console_output(job_name, build_number)
        except jenkins.JenkinsException as exc:
            logger.error("Failed to fetch log for %s #%d: %s", job_name, build_number, exc)
            raise

    def get_build_info(self, job_name: str, build_number: int) -> dict:
        """Fetch build metadata."""
        try:
            return self._server.get_build_info(job_name, build_number)
        except jenkins.JenkinsException as exc:
            logger.error("Failed to fetch build info for %s #%d: %s", job_name, build_number, exc)
            raise

    def get_build_url(self, job_name: str, build_number: int) -> str:
        """Construct the build URL."""
        return f"{self._url}/job/{job_name.replace('/', '/job/')}/{build_number}"

    def parse_log(self, log_text: str) -> list[PipelineStageResult]:
        """Parse a Jenkins log into pipeline stage results."""
        stages = _identify_stages(log_text)
        if not stages:
            # If no pipeline stages detected, treat entire log as a single unknown stage
            error_lines = _extract_error_lines(log_text)
            triggered = _extract_triggered_jobs(log_text)
            status = "fail" if error_lines else "unknown"
            return [
                PipelineStageResult(
                    stage=PipelineStage.UNKNOWN,
                    status=status,
                    error_summary="\n".join(error_lines[:50]) if error_lines else None,
                    log_excerpt=_tail_log(log_text, 200),
                    triggered_jobs=triggered,
                )
            ]
        return stages

    def get_triggered_job_log(self, job_name: str, build_number: int) -> Optional[str]:
        """Fetch the log of a triggered downstream job."""
        try:
            return self.get_build_log(job_name, build_number)
        except Exception:
            logger.warning("Could not fetch triggered job log for %s #%d", job_name, build_number)
            return None


def parse_log_text(log_text: str) -> list[PipelineStageResult]:
    """Parse raw Jenkins log text without requiring a Jenkins connection.

    This is useful when logs are provided directly (e.g., from a file or API).
    """
    return _identify_stages(log_text) or [
        PipelineStageResult(
            stage=PipelineStage.UNKNOWN,
            status="fail" if _extract_error_lines(log_text) else "unknown",
            error_summary="\n".join(_extract_error_lines(log_text)[:50]) or None,
            log_excerpt=_tail_log(log_text, 200),
            triggered_jobs=_extract_triggered_jobs(log_text),
        )
    ]


def _identify_stages(log_text: str) -> list[PipelineStageResult]:
    """Identify pipeline stages from log text."""
    lines = log_text.splitlines()
    current_stage: Optional[PipelineStage] = None
    stage_lines: dict[PipelineStage, list[str]] = {}
    stage_order: list[PipelineStage] = []

    for line in lines:
        detected = _detect_stage(line)
        if detected and detected != current_stage:
            current_stage = detected
            if detected not in stage_lines:
                stage_lines[detected] = []
                stage_order.append(detected)
        if current_stage:
            stage_lines.setdefault(current_stage, []).append(line)

    results: list[PipelineStageResult] = []
    for stage in stage_order:
        block = "\n".join(stage_lines[stage])
        error_lines = _extract_error_lines(block)
        triggered = _extract_triggered_jobs(block)
        status = "fail" if error_lines else "pass"
        results.append(
            PipelineStageResult(
                stage=stage,
                status=status,
                error_summary="\n".join(error_lines[:30]) if error_lines else None,
                log_excerpt=_tail_log(block, 100),
                triggered_jobs=triggered,
            )
        )
    return results


def _detect_stage(line: str) -> Optional[PipelineStage]:
    """Detect which pipeline stage a log line belongs to."""
    for stage, patterns in _STAGE_PATTERNS.items():
        for pat in patterns:
            if pat.search(line):
                return stage
    return None


def _extract_error_lines(text: str) -> list[str]:
    """Extract lines that match error patterns."""
    errors: list[str] = []
    for line in text.splitlines():
        for pat in _ERROR_PATTERNS:
            if pat.search(line):
                errors.append(line.strip())
                break
    return errors


def _extract_triggered_jobs(text: str) -> list[TriggeredJobInfo]:
    """Extract triggered downstream job information from log text."""
    jobs: dict[str, TriggeredJobInfo] = {}

    for match in _TRIGGERED_JOB_PATTERN.finditer(text):
        name, number = match.group(1), int(match.group(2))
        jobs[f"{name}#{number}"] = TriggeredJobInfo(job_name=name, build_number=number, status="unknown")

    for match in _TRIGGERED_JOB_RESULT_PATTERN.finditer(text):
        name, number, result = match.group(1), int(match.group(2)), match.group(3)
        key = f"{name}#{number}"
        if key in jobs:
            jobs[key].status = result.lower()
        else:
            jobs[key] = TriggeredJobInfo(job_name=name, build_number=number, status=result.lower())

    return list(jobs.values())


def _tail_log(text: str, max_lines: int) -> str:
    """Return the last N lines of a log."""
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])
