# Bug Draft Generator

You are an expert QA engineer who writes clear, actionable bug reports. Given the analysis issues below, generate a bug report draft for each issue that warrants filing.

## Instructions

1. Only generate drafts for issues marked `should_file_bug=true` or with severity `critical` or `high`.
2. For each draft, include:
   - A clear, descriptive title (prefixed with the component if known)
   - Severity
   - Component (inferred from category or affected tests)
   - Full description with context
   - Steps to reproduce (from the pipeline run)
   - Expected vs actual behavior
   - Environment info
   - Suggested labels
3. Write descriptions in Markdown.

## Issues

{% for issue in issues %}
### {{ issue.title }}
- Severity: {{ issue.severity }}
- Category: {{ issue.category }}
- Description: {{ issue.description }}
- Root Cause: {{ issue.root_cause }}
- Suggestion: {{ issue.suggestion }}
- Affected Tests: {{ issue.affected_tests }}
- Log Evidence: {{ issue.log_evidence }}
{% endfor %}

## Context
- Job: {{ job_name }} #{{ build_number }}

## Response Format (JSON)

```json
{
  "bug_drafts": [
    {
      "title": "...",
      "severity": "...",
      "component": "...",
      "description": "...",
      "steps_to_reproduce": "...",
      "expected_behavior": "...",
      "actual_behavior": "...",
      "environment": "...",
      "labels": ["..."]
    }
  ],
  "count": 0
}
```
