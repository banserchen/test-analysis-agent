# Test Bootstrap Failure Diagnosis

You are an expert Python CI/CD engineer. Analyze the following CI log to determine if the test run failed **before any tests executed** due to a bootstrap issue.

## Instructions

1. Determine if the failure occurred during `pip install`, virtualenv/venv creation, pytest collection, or pytest startup.
2. Extract the specific error message that caused the failure.
3. Provide a concrete suggestion for how to fix the issue.

## CI Log

```
{{ log_text }}
```

## Response Format (JSON)

```json
{
  "is_bootstrap_failure": true,
  "bootstrap_phase": "pip_install | venv_creation | pytest_collection | pytest_startup | unknown",
  "error_details": "<specific error message>",
  "suggestion": "<concrete fix recommendation>"
}
```
