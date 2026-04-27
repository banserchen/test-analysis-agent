"""Tests for the report generator."""

from datetime import datetime

from test_analysis_agent.models.schemas import (
    AnalysisReport,
    AnalyzedIssue,
    FailureCategory,
    PipelineStage,
    PipelineStageResult,
    Severity,
    TestCaseFailure,
)
from test_analysis_agent.report_generator import (
    generate_html_report,
    generate_json_report,
    generate_markdown_report,
)


def _make_sample_report() -> AnalysisReport:
    """Create a sample report for testing."""
    return AnalysisReport(
        job_name="my-app/deploy-pipeline",
        build_number=42,
        build_url="https://jenkins.example.com/job/my-app/job/deploy-pipeline/42",
        analyzed_at=datetime(2024, 3, 15, 10, 30, 0),
        overall_status="FAILURE",
        failure_stage=PipelineStage.TESTING,
        stage_results=[
            PipelineStageResult(stage=PipelineStage.PREPARATION, status="pass"),
            PipelineStageResult(stage=PipelineStage.DEPLOYMENT, status="pass"),
            PipelineStageResult(
                stage=PipelineStage.TESTING,
                status="fail",
                error_summary="3 tests failed",
            ),
        ],
        total_tests=50,
        passed_tests=47,
        failed_tests=2,
        broken_tests=1,
        skipped_tests=0,
        test_failures=[
            TestCaseFailure(
                test_name="test_login_with_valid_credentials",
                test_class="TestAuth",
                suite_name="auth_suite",
                status="failed",
                error_message="AssertionError: Expected 200, got 500",
                failure_category=FailureCategory.FUNCTION_BUG,
                ai_analysis="The login endpoint is returning 500, likely a server-side bug.",
            ),
        ],
        issues=[
            AnalyzedIssue(
                title="Login API returns 500",
                description="The login endpoint returns HTTP 500 instead of 200",
                category=FailureCategory.FUNCTION_BUG,
                severity=Severity.HIGH,
                stage=PipelineStage.TESTING,
                affected_tests=["test_login_with_valid_credentials"],
                root_cause="Server-side exception in auth handler",
                suggestion="Check the auth service logs for stack traces",
                should_file_bug=True,
                bug_summary="Login API returns 500 for valid credentials",
            ),
        ],
        summary="The pipeline failed during the testing stage. 3 out of 50 tests failed.",
        bug_recommendations=["File a bug for the login API 500 error"],
    )


class TestMarkdownReport:
    """Tests for markdown report generation."""

    def test_contains_job_info(self):
        report = _make_sample_report()
        md = generate_markdown_report(report)
        assert "my-app/deploy-pipeline" in md
        assert "#42" in md

    def test_contains_summary(self):
        report = _make_sample_report()
        md = generate_markdown_report(report)
        assert "testing stage" in md.lower()

    def test_contains_test_results(self):
        report = _make_sample_report()
        md = generate_markdown_report(report)
        assert "50" in md  # total tests
        assert "47" in md  # passed
        assert "test_login_with_valid_credentials" in md

    def test_contains_issues(self):
        report = _make_sample_report()
        md = generate_markdown_report(report)
        assert "Login API returns 500" in md
        assert "HIGH" in md

    def test_contains_bug_recommendations(self):
        report = _make_sample_report()
        md = generate_markdown_report(report)
        assert "Bug Filing" in md


class TestJsonReport:
    """Tests for JSON report generation."""

    def test_valid_json(self):
        report = _make_sample_report()
        import json

        json_str = generate_json_report(report)
        parsed = json.loads(json_str)
        assert parsed["job_name"] == "my-app/deploy-pipeline"
        assert parsed["build_number"] == 42
        assert len(parsed["issues"]) == 1

    def test_contains_all_fields(self):
        report = _make_sample_report()
        import json

        parsed = json.loads(generate_json_report(report))
        assert "failure_stage_name" in parsed
        assert "test_failures" in parsed
        assert "bug_recommendations" in parsed


class TestHtmlReport:
    """Tests for HTML report generation."""

    def test_valid_html(self):
        report = _make_sample_report()
        html = generate_html_report(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_contains_content(self):
        report = _make_sample_report()
        html = generate_html_report(report)
        assert "my-app/deploy-pipeline" in html
        assert "Login API returns 500" in html
