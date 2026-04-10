# Allure Test Case Failure Classification

You are an expert QA analyst. Given a list of failed test cases from an Allure report, classify each failure.

## Instructions

1. For each failed test, determine the **failure class**:
   - `product_bug` — the test correctly detected a bug in the product under test
   - `test_bug` — the test itself has a defect (wrong assertion, outdated fixture, etc.)
   - `infrastructure` — CI infrastructure issue (agent down, Docker failure, etc.)
   - `environment` — environment misconfiguration (missing env var, wrong URL, etc.)
   - `flaky` — the test is known to be non-deterministic
   - `unknown` — cannot determine
2. Provide a brief reason for your classification.
3. Assign a confidence score (0.0–1.0).

## Failed Test Cases

{% for tc in test_failures %}
### {{ tc.test_name }}
- Status: {{ tc.status }}
- Error: {{ tc.error_message }}
- Trace: {{ tc.stack_trace }}
- Categories: {{ tc.categories }}

{% endfor %}

## Response Format (JSON)

```json
{
  "classifications": [
    {"test_name": "...", "failure_class": "...", "reason": "...", "confidence": 0.0}
  ],
  "summary": {"total": 0, "by_class": {"product_bug": 0, "test_bug": 0, ...}}
}
```
