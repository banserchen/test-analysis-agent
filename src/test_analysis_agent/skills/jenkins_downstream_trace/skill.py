"""技能实现：Jenkins 下游 Job 调用链追踪。"""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

# 匹配常见 Jenkins 触发 Job 日志行的模式
_TRIGGER_PATTERNS: list[re.Pattern[str]] = [
    # "Starting building: folder/job-name #123"
    re.compile(r"Starting building:\s+(?P<job>[^\s#]+)\s+#(?P<num>\d+)", re.IGNORECASE),
    # "Triggering a new build of folder/job-name #123"
    re.compile(r"Triggering\s+.*?build\s+of\s+(?P<job>[^\s#]+)\s+#(?P<num>\d+)", re.IGNORECASE),
    # "Build folder/job-name #123 started"
    re.compile(r"Build\s+(?P<job>[^\s#]+)\s+#(?P<num>\d+)\s+started", re.IGNORECASE),
    # "Scheduling project: folder/job-name"
    re.compile(r"Scheduling project:\s+(?P<job>[^\s#]+)", re.IGNORECASE),
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

# Tokens that must never be treated as a job name. They show up when a greedy
# regex accidentally matches the keyword before the real job (e.g. the literal
# "building:" from "Starting building: <job> #<num>").
_RESERVED_JOB_TOKENS = {"building", "building:", "build", "job", "project", "project:"}


class JenkinsDownstreamTraceSkill(BaseSkill):
    """追踪 Jenkins 主子 Job 调用链，定位导致失败的下游任务。"""

    name = "jenkins_downstream_trace"
    description = "追踪 Jenkins 主子 Job 调用链，定位导致失败的下游任务"
    version = "1.1.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        log_text = context.get("log_text", "")
        # 仅当日志中存在触发/下游 Job 的引用时才启用
        return bool(log_text) and bool(
            re.search(r"(?:Starting building|Triggering|Scheduling project|triggered)", log_text, re.IGNORECASE)
        )

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        log_text: str = context.get("log_text", "")
        lines = log_text.splitlines()

        triggered: dict[tuple[str, int], dict[str, Any]] = {}  # keyed by (job_name, build_number)

        # 第一遍：查找触发的 Job
        for idx, line in enumerate(lines):
            for pat in _TRIGGER_PATTERNS:
                m = pat.search(line)
                if not m:
                    continue
                job = m.group("job").strip().strip("'\"").rstrip(":")
                if not job or job.lower() in _RESERVED_JOB_TOKENS:
                    continue
                num_str = m.groupdict().get("num")
                num = int(num_str) if num_str else 0
                key = (job, num)
                if key not in triggered:
                    triggered[key] = {
                        "job_name": job,
                        "build_number": num,
                        "status": "unknown",
                        "trigger_line": idx + 1,
                    }
                break

        # 也从 stage_results 中提取（如有提供）
        stage_results = context.get("stage_results", [])
        if stage_results:
            for stage in stage_results:
                for tj in getattr(stage, "triggered_jobs", []):
                    if tj.job_name.lower() in _RESERVED_JOB_TOKENS:
                        continue
                    key = (tj.job_name, tj.build_number)
                    if key not in triggered:
                        triggered[key] = {
                            "job_name": tj.job_name,
                            "build_number": tj.build_number,
                            "status": tj.status,
                            "trigger_line": 0,
                        }
                    else:
                        triggered[key]["status"] = tj.status

        # 第二遍：查找完成状态
        for line in lines:
            for pat in _RESULT_PATTERNS:
                m = pat.search(line)
                if not m:
                    continue
                groups = m.groupdict()
                job = (groups.get("job") or "").strip().strip("'\"").rstrip(":")
                status = (groups.get("status") or "unknown").lower()
                if not job or job.lower() in _RESERVED_JOB_TOKENS:
                    continue
                num_str = groups.get("num")
                if num_str is None:
                    continue
                key = (job, int(num_str))
                if key in triggered:
                    triggered[key]["status"] = status

        call_chain = sorted(triggered.values(), key=lambda x: x.get("trigger_line", 0))

        # 构建失败链路
        failing_chain: list[str] = [
            entry["job_name"] for entry in call_chain if entry["status"] in ("failure", "unstable")
        ]

        # 摘要
        if failing_chain:
            summary = (
                f"下游调用链共触发 {len(call_chain)} 个 Job。"
                f"失败路径：{' → '.join(failing_chain)}。"
            )
        elif call_chain:
            summary = f"下游调用链共触发 {len(call_chain)} 个 Job，全部成功。"
        else:
            summary = "日志中未检测到下游 Job。"

        return {
            "call_chain": call_chain,
            "failing_chain": failing_chain,
            "summary": summary,
        }

