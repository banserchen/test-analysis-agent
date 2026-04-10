"""Skill implementation: Jenkins downstream job call chain tracing."""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

# Patterns matching common Jenkins triggered-job log lines
_TRIGGER_PATTERNS: list[re.Pattern[str]] = [
    # "Starting building: folder/job-name #123"
    re.compile(r"Starting building:\s+(?P<job>[^\s#]+)\s+#(?P<num>\d+)", re.IGNORECASE),
    # "Triggering a new build of folder/job-name #123"
    re.compile(r"Triggering\s+.*?build\s+of\s+(?P<job>[^\s#]+)\s+#(?P<num>\d+)", re.IGNORECASE),
    # "Build folder/job-name #123 started"
    re.compile(r"Build\s+(?P<job>[^\s#]+)\s+#(?P<num>\d+)\s+started", re.IGNORECASE),
]

_RESULT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?P<job>[^\s#]+)\s+#(?P<num>\d+)\s+completed.*?(?P<status>SUCCESS|FAILURE|UNSTABLE|ABORTED)",
        re.IGNORECASE,
    ),
    re.compile(
        r"Finished:\s+(?P<status>SUCCESS|FAILURE|UNSTABLE|ABORTED)",
        re.IGNORECASE,
    ),
]


class JenkinsDownstreamTraceSkill(BaseSkill):
    """Trace parent-child Jenkins job call chains and identify which downstream job caused the failure."""

    name = "jenkins_downstream_trace"
    description = "Trace parent-child Jenkins job call chains and identify which downstream job caused the failure"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        log_text = context.get("log_text", "")
        # Only useful when there are triggered / downstream job references
        return bool(log_text) and bool(
            re.search(r"(?:Starting building|Triggering|triggered)", log_text, re.IGNORECASE)
        )

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        log_text: str = context.get("log_text", "")
        lines = log_text.splitlines()

        triggered: dict[str, dict[str, Any]] = {}  # keyed by job_name

        # Pass 1: find triggered jobs
        for idx, line in enumerate(lines):
            for pat in _TRIGGER_PATTERNS:
                m = pat.search(line)
                if m:
                    job = m.group("job")
                    num = int(m.group("num"))
                    triggered[job] = {
                        "job_name": job,
                        "build_number": num,
                        "status": "unknown",
                        "trigger_line": idx + 1,
                    }
                    break

        # Also pull from stage_results if provided
        stage_results = context.get("stage_results", [])
        if stage_results:
            for stage in stage_results:
                for tj in getattr(stage, "triggered_jobs", []):
                    if tj.job_name not in triggered:
                        triggered[tj.job_name] = {
                            "job_name": tj.job_name,
                            "build_number": tj.build_number,
                            "status": tj.status,
                            "trigger_line": 0,
                        }
                    else:
                        # Enrich status from stage_results
                        triggered[tj.job_name]["status"] = tj.status

        # Pass 2: find completion statuses
        for line in lines:
            for pat in _RESULT_PATTERNS:
                m = pat.search(line)
                if m:
                    groups = m.groupdict()
                    job = groups.get("job", "")
                    status = groups.get("status", "unknown").lower()
                    if job and job in triggered:
                        triggered[job]["status"] = status

        call_chain = sorted(triggered.values(), key=lambda x: x.get("trigger_line", 0))

        # Build failing chain
        failing_chain: list[str] = []
        for entry in call_chain:
            if entry["status"] in ("failure", "unstable"):
                failing_chain.append(entry["job_name"])

        # Summary
        if failing_chain:
            summary = (
                f"Downstream call chain has {len(call_chain)} triggered job(s). "
                f"Failing path: {' → '.join(failing_chain)}."
            )
        elif call_chain:
            summary = f"Downstream call chain has {len(call_chain)} triggered job(s), all succeeded."
        else:
            summary = "No downstream jobs detected in the log."

        return {
            "call_chain": call_chain,
            "failing_chain": failing_chain,
            "summary": summary,
        }
