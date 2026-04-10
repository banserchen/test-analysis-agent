"""技能实现：栈追踪到代码文件/行级定位。"""

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
    """将栈追踪帧映射到源代码文件路径和行号。"""

    name = "code_stacktrace_mapper"
    description = "将栈追踪帧映射到源代码文件路径和行号"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        # 在 test_failures 或直接上下文中查找栈追踪
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

        # 如果没有直接的 stack_trace，从 test_failures 中收集
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

        # 查找最深层的用户代码帧
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
    """解析栈追踪，返回 (语言, 帧列表)。"""
    # 尝试检测语言
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
        # 尽力尝试：使用所有模式
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
    """检查文件路径或类名是否属于标准库/第三方库。"""
    markers = _STDLIB_MARKERS.get(language, [])
    lower = path_or_name.lower()
    return any(marker.lower() in lower for marker in markers)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
