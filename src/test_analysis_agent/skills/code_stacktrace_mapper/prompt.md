# Stack Trace to Code Mapper

You are an expert at reading stack traces across multiple programming languages. Given a raw stack trace, extract every frame and map it to a file, line number, and function name.

## Instructions

1. Detect the language (Python, Java, JavaScript/Node.js, etc.).
2. Parse each stack frame into: file path, line number, function/method name.
3. Determine whether each frame is **user code** (project code) or library/framework/stdlib code.
4. Identify the **deepest user frame** — the most specific project frame closest to the error.

## Stack Trace

```
{{ stack_trace }}
```

{% if language_hint and language_hint != "auto" %}
Language hint: {{ language_hint }}
{% endif %}

## Response Format (JSON)

```json
{
  "frames": [
    {"file": "...", "line": 0, "function": "...", "code_snippet": "", "is_user_code": true}
  ],
  "language": "python",
  "deepest_user_frame": {"file": "...", "line": 0, "function": "..."}
}
```
