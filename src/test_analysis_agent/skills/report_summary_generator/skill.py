"""Skill implementation: Dual-version report generation (management + developer)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from test_analysis_agent.models.schemas import AnalysisReport
from test_analysis_agent.skills.base import BaseSkill


class ReportSummaryGeneratorSkill(BaseSkill):
    """Generate dual-version reports: a management summary and a detailed developer report."""

    name = "report_summary_generator"
    description = "Generate dual-version reports: management summary and developer report"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        return context.get("report") is not None or context.get("issues") is not None

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        report = context.get("report")
        issues = context.get("issues", [])

        if isinstance(report, AnalysisReport):
            return self._from_report(report)

        # Fallback: work from issues list directly
        return self._from_issues(issues, context)

    def _from_report(self, report: AnalysisReport) -> dict[str, Any]:
        """Generate dual reports from a full AnalysisReport."""
        total = report.total_tests
        passed = report.passed_tests
        pass_rate = f"{(passed / total * 100):.1f}%" if total > 0 else "N/A"

        critical_count = sum(1 for i in report.issues if i.severity.value in ("critical", "high"))
        bug_rec_count = len(report.bug_recommendations)

        # Risk level
        if any(i.severity.value == "critical" for i in report.issues):
            risk = "critical"
            emoji = "🔴"
        elif any(i.severity.value == "high" for i in report.issues):
            risk = "high"
            emoji = "🟠"
        elif report.issues:
            risk = "medium"
            emoji = "🟡"
        else:
            risk = "low"
            emoji = "🟢"

        # Management summary
        action_items: list[str] = []
        for issue in report.issues:
            if issue.should_file_bug:
                action_items.append(f"File bug: {issue.bug_summary or issue.title}")
            if issue.suggestion:
                action_items.append(issue.suggestion)

        mgmt_body = (
            f"Pipeline **{report.job_name} #{report.build_number}** "
            f"completed with status **{report.overall_status}**. "
            f"Failure occurred in the **{report.failure_stage.value}** stage.\n\n"
        )
        if total > 0:
            mgmt_body += (
                f"Test execution: {total} tests, {passed} passed ({pass_rate} pass rate), "
                f"{report.failed_tests} failed, {report.broken_tests} broken.\n\n"
            )
        if report.issues:
            mgmt_body += (
                f"{len(report.issues)} issue(s) identified, {critical_count} critical/high severity. "
                f"{bug_rec_count} bug report(s) recommended."
            )

        # Developer report
        category_counter: Counter[str] = Counter()
        category_issues: dict[str, list[str]] = {}
        for issue in report.issues:
            cat = issue.category.value
            category_counter[cat] += 1
            category_issues.setdefault(cat, []).append(issue.title)

        breakdown = [
            {"category": cat, "count": cnt, "issues": category_issues.get(cat, [])}
            for cat, cnt in category_counter.most_common()
        ]

        detailed_parts: list[str] = []
        for issue in report.issues:
            part = (
                f"### [{issue.severity.value.upper()}] {issue.title}\n\n"
                f"**Category:** {issue.category.value}  \n"
                f"**Stage:** {issue.stage.value}  \n"
                f"**Description:** {issue.description}\n\n"
            )
            if issue.root_cause:
                part += f"**Root Cause:** {issue.root_cause}\n\n"
            if issue.suggestion:
                part += f"**Suggestion:** {issue.suggestion}\n\n"
            if issue.log_evidence:
                part += f"**Evidence:**\n```\n{issue.log_evidence}\n```\n\n"
            detailed_parts.append(part)

        next_steps: list[str] = []
        for issue in report.issues:
            if issue.suggestion:
                next_steps.append(f"[{issue.category.value}] {issue.suggestion}")
        if bug_rec_count > 0:
            next_steps.append(f"File {bug_rec_count} recommended bug report(s)")

        return {
            "management_summary": {
                "title": f"Pipeline Report: {report.job_name} #{report.build_number}",
                "status_emoji": emoji,
                "one_liner": (
                    f"{report.job_name} #{report.build_number} — "
                    f"{report.overall_status}, {critical_count} critical/high issue(s)"
                ),
                "key_metrics": {
                    "total_tests": total,
                    "pass_rate": pass_rate,
                    "critical_issues": critical_count,
                    "bug_recommendations": bug_rec_count,
                },
                "risk_level": risk,
                "action_items": action_items[:10],
                "body": mgmt_body,
            },
            "developer_report": {
                "title": f"Technical Analysis: {report.job_name} #{report.build_number}",
                "failure_breakdown": breakdown,
                "detailed_findings": "\n".join(detailed_parts) or "No issues found.",
                "next_steps": next_steps[:10],
            },
        }

    def _from_issues(self, issues: list[Any], context: dict[str, Any]) -> dict[str, Any]:
        """Fallback: generate reports from a list of issues."""
        job_name = context.get("job_name", "unknown")
        build_number = context.get("build_number", 0)

        critical_count = sum(
            1 for i in issues
            if _get_severity(i) in ("critical", "high")
        )

        risk = "high" if critical_count > 0 else ("medium" if issues else "low")
        emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(risk, "⚪")

        action_items = []
        for issue in issues:
            suggestion = _get(issue, "suggestion", "")
            if suggestion:
                action_items.append(suggestion)

        return {
            "management_summary": {
                "title": f"Pipeline Report: {job_name} #{build_number}",
                "status_emoji": emoji,
                "one_liner": f"{job_name} #{build_number} — {len(issues)} issue(s) found",
                "key_metrics": {
                    "total_tests": 0,
                    "pass_rate": "N/A",
                    "critical_issues": critical_count,
                    "bug_recommendations": 0,
                },
                "risk_level": risk,
                "action_items": action_items[:10],
                "body": f"{len(issues)} issue(s) identified, {critical_count} critical/high severity.",
            },
            "developer_report": {
                "title": f"Technical Analysis: {job_name} #{build_number}",
                "failure_breakdown": [],
                "detailed_findings": "Issues provided without full report context.",
                "next_steps": action_items[:10],
            },
        }

    def post_process(self, report: AnalysisReport) -> AnalysisReport:
        """No post-processing — this skill generates standalone output."""
        return report


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_severity(issue: Any) -> str:
    sev = _get(issue, "severity", "medium")
    if hasattr(sev, "value"):
        return sev.value
    return str(sev)
