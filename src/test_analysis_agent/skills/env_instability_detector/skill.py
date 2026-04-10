"""技能实现：环境不稳定性检测。"""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

_INSTABILITY_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    # (category, severity, pattern)
    ("network", "high", re.compile(
        r"(?:Connection reset by peer|ECONNRESET|connect ETIMEDOUT|"
        r"Connection timed out|Connection refused|ECONNREFUSED|"
        r"Network is unreachable|ENETUNREACH)",
        re.IGNORECASE,
    )),
    ("dns", "high", re.compile(
        r"(?:Name or service not known|getaddrinfo.*ENOTFOUND|"
        r"DNS resolution failed|Temporary failure in name resolution|"
        r"could not resolve host)",
        re.IGNORECASE,
    )),
    ("dependency_source", "high", re.compile(
        r"(?:(?:pypi|npm|maven|registry).*(?:503|502|500|timeout|unavailable)|"
        r"Could not fetch URL|Retrying \(Retry\(total=|"
        r"ERR! network|ETARGET|E404.*registry)",
        re.IGNORECASE,
    )),
    ("permission", "medium", re.compile(
        r"(?:Permission denied|EACCES|Operation not permitted|"
        r"Access denied|Forbidden|chmod.*failed)",
        re.IGNORECASE,
    )),
    ("disk", "high", re.compile(
        r"(?:No space left on device|ENOSPC|Disk quota exceeded|"
        r"write failed.*space|Cannot allocate.*disk)",
        re.IGNORECASE,
    )),
    ("memory", "high", re.compile(
        r"(?:Out of memory|OOM|Cannot allocate memory|MemoryError|"
        r"killed.*memory|Killed process)",
        re.IGNORECASE,
    )),
    ("clock_skew", "medium", re.compile(
        r"(?:clock skew|certificate.*not yet valid|"
        r"certificate.*expired|time.{0,20}(?:mismatch|sync))",
        re.IGNORECASE,
    )),
    ("certificate", "medium", re.compile(
        r"(?:SSL.*(?:error|fail|verify)|CERT_.*|"
        r"certificate verify failed|unable to get local issuer|"
        r"self-signed certificate|UNABLE_TO_VERIFY_LEAF_SIGNATURE)",
        re.IGNORECASE,
    )),
]


class EnvInstabilityDetectorSkill(BaseSkill):
    """检测 CI 日志中的环境不稳定信号。"""

    name = "env_instability_detector"
    description = "检测环境不稳定性——网络、依赖源、权限等"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        return bool(context.get("log_text"))

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        log_text: str = context.get("log_text", "")

        instabilities: list[dict[str, Any]] = []
        seen_categories: set[str] = set()

        for category, severity, pattern in _INSTABILITY_PATTERNS:
            matches = pattern.findall(log_text)
            if matches:
                if category not in seen_categories:
                    instabilities.append({
                        "category": category,
                        "evidence": matches[0] if matches else "",
                        "occurrence_count": len(matches),
                        "severity": severity,
                    })
                    seen_categories.add(category)

        is_unstable = len(instabilities) > 0

        if instabilities:
            cats = ", ".join(sorted(seen_categories))
            summary = f"检测到 {len(instabilities)} 类环境不稳定信号：{cats}。"
        else:
            summary = "未检测到环境不稳定信号。"

        return {
            "instabilities": instabilities,
            "is_unstable": is_unstable,
            "summary": summary,
        }
