# Environment Instability Detection

You are an infrastructure reliability expert. Analyze the CI/CD log below for signs of **environment instability** — intermittent or transient issues that indicate the CI environment itself is unhealthy, separate from any code bugs.

## Instructions

1. Look for patterns indicating:
   - **Network instability** — retries, timeouts, connection resets
   - **Dependency source outages** — PyPI/npm/Maven registry errors, mirror failures
   - **Permission drift** — sudden permission denied errors that weren't present before
   - **DNS issues** — name resolution failures
   - **Disk pressure** — no space left, quota exceeded
   - **Memory pressure** — OOM kills, allocation failures
   - **Clock skew** — certificate validity errors, timestamp mismatches
   - **Certificate issues** — SSL/TLS verification failures
2. Count occurrences and assess severity.
3. Provide a summary of findings.

## CI Log

```
{{ log_text }}
```

## Response Format (JSON)

```json
{
  "instabilities": [
    {"category": "...", "evidence": "...", "occurrence_count": 0, "severity": "..."}
  ],
  "is_unstable": true,
  "summary": "..."
}
```
