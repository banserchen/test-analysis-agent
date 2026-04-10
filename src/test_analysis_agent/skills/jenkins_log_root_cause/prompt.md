# Jenkins Log Root-Cause Analysis

You are an expert Jenkins CI/CD log analyst. Given the following Jenkins console log, identify the **root cause** of the build failure.

## Instructions

1. Scan the log for error-level messages, stack traces, and fatal exit codes.
2. For each distinct error, extract a minimal snippet (≤ {{ max_snippet_lines }} lines) that fully captures the error context.
3. Classify each error into one of: `compilation`, `runtime`, `dependency`, `timeout`, `permission`, `network`, `resource`, `configuration`, `unknown`.
4. Assign a confidence score (0.0–1.0) indicating how likely this snippet is the **true root cause** (not a cascading symptom).
5. Provide a one-sentence `primary_error` summary for the most likely root cause.

## Log

```
{{ log_text }}
```

## Response Format (JSON)

```json
{
  "root_cause_snippets": [
    {
      "line_start": <int>,
      "line_end": <int>,
      "text": "<snippet>",
      "error_type": "<type>",
      "confidence": <float>
    }
  ],
  "primary_error": "<one sentence>"
}
```
