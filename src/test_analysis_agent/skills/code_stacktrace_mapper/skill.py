"""Skill implementation: Stack trace to code file/line mapping."""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.skills.base import BaseSkill

# Python: File "/path/to/file.py", line 42, in func_name
_PYTHON_FRAME = re.compile(
    r'File "(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<func>\S+)'
)

# Java: at com.example.Class.method(File.java:42)
_JAVA_FRAME = re.compile(
    r"at\s+(?P<func>[\w$.]+)\((?P<file>[^:)]+):(?P<line>\d+)\)"
)

# JavaScript/Node: at funcName (/path/to/file.js:42:10)
_JS_FRAME = re.compile(
    r"at\s+(?:(?P<func>[\w.<>]+)\s+)?\(?(?P<file>[^:()]+):(?P<line>\d+):\d+\)?"
)

# Standard library / third-party markers
_STDLIB_MARKERS = {
    "python": ["/lib/python", "/site-packages/", "/dist-packages/", "<frozen", "<string>"],
    "java": ["java.lang.", "java.util.", "sun.", "com.sun.", "org.junit.", "org.apache.maven."],
    "javascript": ["node_modules/", "internal/", "<anonymous>"],
}


class CodeStacktraceMapperSkill(BaseSkill):
    """Map stack trace frames to source code file paths and line numbers."""

    name = "code_stacktrace_mapper"
    description = "Map stack trace frames to source code file paths and line numbers"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        # Look for stack traces in test_failures or directly in context
        if context.get("stack_trace"):
            return True
        failures = context.get("test_failures", [])
        return any(
            _get(f, "stack_trace") for f in failures
            if isinstance(f, (dict, object))
        )

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stack_trace = context.get("stack_trace", "")
        language_hint = context.get("language_hint", "auto")

        # If no direct stack_trace, collect from test_failures
        if not stack_trace:
            traces: list[str] = []
            for f in context.get("test_failures", []):
                st = _get(f, "stack_trace")
                if st:
                    traces.append(st)
            stack_trace = "\n---\n".join(traces)

        if not stack_trace:
            return {"frames": [], "language": "unknown", "deepest_user_frame": None}

        language, frames = _parse_stack_trace(stack_trace, language_hint)

        # Find deepest user frame
        deepest = None
        for frame in reversed(frames):
            if frame["is_user_code"]:
                deepest = {"file": frame["file"], "line": frame["line"], "function": frame["function"]}
                break

        return {
            "frames": frames,
            "language": language,
            "deepest_user_frame": deepest,
        }


def _parse_stack_trace(text: str, hint: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse a stack trace and return (language, frames)."""
    # Try to detect language
    if hint != "auto":
        language = hint
    elif _PYTHON_FRAME.search(text):
        language = "python"
    elif _JAVA_FRAME.search(text):
        language = "java"
    elif _JS_FRAME.search(text):
        language = "javascript"
    else:
        language = "unknown"

    frames: list[dict[str, Any]] = []

    if language == "python":
        for m in _PYTHON_FRAME.finditer(text):
            filepath = m.group("file")
            frames.append({
                "file": filepath,
                "line": int(m.group("line")),
                "function": m.group("func"),
                "code_snippet": "",
                "is_user_code": not _is_stdlib(filepath, "python"),
            })
    elif language == "java":
        for m in _JAVA_FRAME.finditer(text):
            func = m.group("func")
            filepath = m.group("file")
            frames.append({
                "file": filepath,
                "line": int(m.group("line")),
                "function": func,
                "code_snippet": "",
                "is_user_code": not _is_stdlib(func, "java"),
            })
    elif language == "javascript":
        for m in _JS_FRAME.finditer(text):
            filepath = m.group("file")
            frames.append({
                "file": filepath,
                "line": int(m.group("line")),
                "function": m.group("func") or "<anonymous>",
                "code_snippet": "",
                "is_user_code": not _is_stdlib(filepath, "javascript"),
            })
    else:
        # Best effort: try all patterns
        for m in _PYTHON_FRAME.finditer(text):
            frames.append({
                "file": m.group("file"),
                "line": int(m.group("line")),
                "function": m.group("func"),
                "code_snippet": "",
                "is_user_code": True,
            })

    return language, frames


def _is_stdlib(path_or_name: str, language: str) -> bool:
    """Check if a file path or class name belongs to stdlib/third-party."""
    markers = _STDLIB_MARKERS.get(language, [])
    lower = path_or_name.lower()
    return any(marker.lower() in lower for marker in markers)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
