# Jenkins Downstream Job Trace

You are an expert at analyzing Jenkins pipeline call chains. Given the parent job's console log, reconstruct the full downstream job invocation tree.

## Instructions

1. Identify every downstream/triggered job from patterns like `Triggering`, `Starting building`, `Waiting for completion`, `build` step references.
2. For each job, determine its name, build number, and final status.
3. Build an ordered call chain list.
4. Identify the **failing chain** — the path from the root job through child jobs to the leaf that failed.
5. Write a human-readable summary.

## Parent Job Log

```
{{ log_text }}
```

{% if stage_results %}
## Parsed Stage Results

{{ stage_results }}
{% endif %}

## Response Format (JSON)

```json
{
  "call_chain": [
    {"job_name": "...", "build_number": 0, "status": "...", "trigger_line": 0}
  ],
  "failing_chain": ["parent-job", "child-job", "leaf-job"],
  "summary": "..."
}
```
