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
    """追踪 Jenkins 主子 Job 调用链，定位导致失败的下游任务。"""

    name = "jenkins_downstream_trace"
    description = "追踪 Jenkins 主子 Job 调用链，定位导致失败的下游任务"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        log_text = context.get("log_text", "")
        # 仅当日志中存在触发/下游 Job 的引用时才启用
        return bool(log_text) and bool(
            re.search(r"(?:Starting building|Triggering|triggered)", log_text, re.IGNORECASE)
        )

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        log_text: str = context.get("log_text", "")
        lines = log_text.splitlines()

        triggered: dict[str, dict[str, Any]] = {}  # keyed by job_name

        # 第一遍：查找触发的 Job
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

            # 也从 stage_results 中提取（如有提供）
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
                        # 用 stage_results 丰富状态信息
                        triggered[tj.job_name]["status"] = tj.status

        # 第二遍：查找完成状态
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

        # 构建失败链路
        failing_chain: list[str] = []
        for entry in call_chain:
            if entry["status"] in ("failure", "unstable"):
                failing_chain.append(entry["job_name"])

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
