"""技能实现：生成双版本报告（管理层摘要 + 研发详细报告）。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from test_analysis_agent.models.schemas import AnalysisReport
from test_analysis_agent.skills.base import BaseSkill


class ReportSummaryGeneratorSkill(BaseSkill):
    """生成双版本报告：管理层摘要和研发详细报告。"""

    name = "report_summary_generator"
    description = "生成双版本报告：管理层摘要和研发详细报告"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        return context.get("report") is not None or context.get("issues") is not None

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        report = context.get("report")
        issues = context.get("issues", [])

        if isinstance(report, AnalysisReport):
            return self._from_report(report)

        # 回退：从 issues 列表直接生成
        return self._from_issues(issues, context)

    def _from_report(self, report: AnalysisReport) -> dict[str, Any]:
        """从完整的 AnalysisReport 生成双版本报告。"""
        total = report.total_tests
        passed = report.passed_tests
        pass_rate = f"{(passed / total * 100):.1f}%" if total > 0 else "N/A"

        critical_count = sum(1 for i in report.issues if i.severity.value in ("critical", "high"))
        bug_rec_count = len(report.bug_recommendations)

        # 风险等级
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

        # 管理层摘要
        action_items: list[str] = []
        for issue in report.issues:
            if issue.should_file_bug:
                action_items.append(f"提交缺陷：{issue.bug_summary or issue.title}")
            if issue.suggestion:
                action_items.append(issue.suggestion)

        mgmt_body = (
            f"流水线 **{report.job_name} #{report.build_number}** "
            f"执行完成，状态为 **{report.overall_status}**。"
            f"失败发生在 **{report.failure_stage.value}** 阶段。\n\n"
        )
        if total > 0:
            mgmt_body += (
                f"测试执行：共 {total} 个用例，{passed} 个通过（通过率 {pass_rate}），"
                f"{report.failed_tests} 个失败，{report.broken_tests} 个异常中断。\n\n"
            )
        if report.issues:
            mgmt_body += (
                f"共发现 {len(report.issues)} 个问题，其中 {critical_count} 个为严重/高优先级。"
                f"建议提交 {bug_rec_count} 个缺陷报告。"
            )

        # 研发详细报告
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
                f"**分类：** {issue.category.value}  \n"
                f"**阶段：** {issue.stage.value}  \n"
                f"**描述：** {issue.description}\n\n"
            )
            if issue.root_cause:
                part += f"**根因：** {issue.root_cause}\n\n"
            if issue.suggestion:
                part += f"**建议：** {issue.suggestion}\n\n"
            if issue.log_evidence:
                part += f"**证据：**\n```\n{issue.log_evidence}\n```\n\n"
            detailed_parts.append(part)

        next_steps: list[str] = []
        for issue in report.issues:
            if issue.suggestion:
                next_steps.append(f"[{issue.category.value}] {issue.suggestion}")
        if bug_rec_count > 0:
            next_steps.append(f"提交 {bug_rec_count} 个建议的缺陷报告")

        return {
            "management_summary": {
                "title": f"流水线报告：{report.job_name} #{report.build_number}",
                "status_emoji": emoji,
                "one_liner": (
                    f"{report.job_name} #{report.build_number} — "
                    f"{report.overall_status}，{critical_count} 个严重/高优先级问题"
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
                "title": f"技术分析：{report.job_name} #{report.build_number}",
                "failure_breakdown": breakdown,
                "detailed_findings": "\n".join(detailed_parts) or "未发现问题。",
                "next_steps": next_steps[:10],
            },
        }

    def _from_issues(self, issues: list[Any], context: dict[str, Any]) -> dict[str, Any]:
        """回退方案：从 issues 列表直接生成报告。"""
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
                "title": f"流水线报告：{job_name} #{build_number}",
                "status_emoji": emoji,
                "one_liner": f"{job_name} #{build_number} — 发现 {len(issues)} 个问题",
                "key_metrics": {
                    "total_tests": 0,
                    "pass_rate": "N/A",
                    "critical_issues": critical_count,
                    "bug_recommendations": 0,
                },
                "risk_level": risk,
                "action_items": action_items[:10],
                "body": f"共发现 {len(issues)} 个问题，其中 {critical_count} 个为严重/高优先级。",
            },
            "developer_report": {
                "title": f"技术分析：{job_name} #{build_number}",
                "failure_breakdown": [],
                "detailed_findings": "问题列表已提供，但缺少完整的报告上下文。",
                "next_steps": action_items[:10],
            },
        }

    def post_process(self, report: AnalysisReport) -> AnalysisReport:
        """无需后处理——此技能生成独立输出。"""
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
