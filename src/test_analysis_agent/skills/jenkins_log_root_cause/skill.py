"""Skill implementation: Jenkins log root-cause analysis."""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

# Patterns that indicate true errors (not just warnings)
_ERROR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:FATAL|SEVERE|CRITICAL)[:\s]", re.IGNORECASE), "runtime"),
    (re.compile(r"(?:error\[?\s*\w*\]?:\s)", re.IGNORECASE), "compilation"),
    (re.compile(r"(?:Exception|Error|Traceback)\s", re.IGNORECASE), "runtime"),
    (re.compile(r"(?:BUILD FAILED|BUILD FAILURE|FAILURE)", re.IGNORECASE), "compilation"),
    (re.compile(r"(?:timed? ?out|deadline exceeded)", re.IGNORECASE), "timeout"),
    (re.compile(r"(?:Permission denied|Access denied)", re.IGNORECASE), "permission"),
    (re.compile(r"(?:Connection refused|ECONNREFUSED|Name or service not known)", re.IGNORECASE), "network"),
    (re.compile(r"(?:No space left on device|Out of memory|OOM)", re.IGNORECASE), "resource"),
    (re.compile(r"(?:ModuleNotFoundError|ImportError|Cannot find module)", re.IGNORECASE), "dependency"),
    (re.compile(r"(?:configuration error|config .{0,60} not found)", re.IGNORECASE), "configuration"),
    (re.compile(r"exit\s+code\s+[1-9]\d{0,2}", re.IGNORECASE), "runtime"),
]

_CONTEXT_LINES = 5  # lines of context before/after an error line


class JenkinsLogRootCauseSkill(BaseSkill):
    """Locate error root causes in Jenkins build logs and extract key failure snippets."""

    name = "jenkins_log_root_cause"
    description = "Locate error root causes in Jenkins build logs and extract key failure snippets"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        return bool(context.get("log_text"))

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        log_text: str = context.get("log_text", "")
        max_snippet = context.get("max_snippet_lines", 30)
        lines = log_text.splitlines()

        raw_hits: list[dict[str, Any]] = []
        for idx, line in enumerate(lines):
            for pattern, error_type in _ERROR_PATTERNS:
                if pattern.search(line):
                    raw_hits.append({"line_idx": idx, "error_type": error_type, "match": line.strip()})
                    break  # one match per line

        # Merge nearby hits into snippets
        snippets: list[dict[str, Any]] = []
        used: set[int] = set()
        for hit in raw_hits:
            if hit["line_idx"] in used:
                continue
            start = max(0, hit["line_idx"] - _CONTEXT_LINES)
            end = min(len(lines), hit["line_idx"] + _CONTEXT_LINES + 1)

            # Expand to include consecutive hits
            for other in raw_hits:
                if other["line_idx"] in used:
                    continue
                if start <= other["line_idx"] < end + max_snippet:
                    end = min(len(lines), max(end, other["line_idx"] + _CONTEXT_LINES + 1))
                    used.add(other["line_idx"])

            # Enforce max_snippet limit
            if end - start > max_snippet:
                end = start + max_snippet

            used.add(hit["line_idx"])
            snippet_text = "\n".join(lines[start:end])
            snippets.append({
                "line_start": start + 1,
                "line_end": end,
                "text": snippet_text,
                "error_type": hit["error_type"],
                "confidence": _confidence_for(hit, lines),
            })

        # Sort by confidence descending
        snippets.sort(key=lambda s: s["confidence"], reverse=True)

        primary = snippets[0]["text"].split("\n")[0] if snippets else "No clear error detected"

        return {
            "root_cause_snippets": snippets,
            "primary_error": primary,
        }


def _confidence_for(hit: dict[str, Any], lines: list[str]) -> float:
    """Heuristic confidence: later errors in the log tend to be cascaded, not root cause."""
    total = len(lines) or 1
    position_ratio = hit["line_idx"] / total  # 0 = top, 1 = bottom

    # Errors in the first half of the log are more likely root causes
    base = 0.8 if position_ratio < 0.5 else 0.5

    # Certain error types are more decisive
    boosts = {"compilation": 0.1, "dependency": 0.1, "permission": 0.05, "network": 0.05}
    base += boosts.get(hit["error_type"], 0.0)

    return round(min(base, 1.0), 2)
