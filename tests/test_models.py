"""Tests for data models."""

from test_analysis_agent.models.schemas import (
    AnalysisReport,
    AnalysisRequest,
    AnalyzedIssue,
    FailureCategory,
    PipelineStage,
    PipelineStageResult,
    Severity,
    TestCaseFailure,
    TriggeredJobInfo,
)


class TestAnalysisRequest:
    """Tests for AnalysisRequest model."""

    def test_minimal_request(self):
        req = AnalysisRequest(job_name="my-job", build_number=1)
        assert req.job_name == "my-job"
        assert req.build_number == 1
        assert req.allure_report_url is None

    def test_full_request(self):
        req = AnalysisRequest(
            jenkins_url="http://jenkins.example.com",
            job_name="folder/my-job",
            build_number=42,
            allure_report_url="http://allure.example.com/report",
            allure_report_path="/tmp/allure-report",
            test_repo_path="/src/tests",
            additional_context="This is a nightly build",
        )
        assert req.jenkins_url == "http://jenkins.example.com"
        assert req.additional_context == "This is a nightly build"


class TestPipelineStageResult:
    """Tests for PipelineStageResult model."""

    def test_pass_stage(self):
        result = PipelineStageResult(stage=PipelineStage.PREPARATION, status="pass")
        assert result.stage == PipelineStage.PREPARATION
        assert result.error_summary is None

    def test_fail_stage_with_triggered_jobs(self):
        result = PipelineStageResult(
            stage=PipelineStage.DEPLOYMENT,
            status="fail",
            error_summary="Deploy failed",
            triggered_jobs=[
                TriggeredJobInfo(job_name="deploy-k8s", build_number=5, status="failure"),
            ],
        )
        assert len(result.triggered_jobs) == 1
        assert result.triggered_jobs[0].status == "failure"


class TestTestCaseFailure:
    """Tests for TestCaseFailure model."""

    def test_basic_failure(self):
        failure = TestCaseFailure(
            test_name="test_login",
            status="failed",
            error_message="Expected 200, got 500",
        )
        assert failure.test_name == "test_login"
        assert failure.failure_category == FailureCategory.UNKNOWN

    def test_failure_with_analysis(self):
        failure = TestCaseFailure(
            test_name="test_api_response",
            status="broken",
            error_message="ConnectionError",
            ai_analysis="The API server was unreachable",
            failure_category=FailureCategory.NETWORK_ERROR,
        )
        assert failure.ai_analysis is not None
        assert failure.failure_category == FailureCategory.NETWORK_ERROR


class TestAnalysisReport:
    """Tests for AnalysisReport model."""

    def test_minimal_report(self):
        report = AnalysisReport(
            job_name="test-job",
            build_number=1,
            overall_status="FAILURE",
            failure_stage=PipelineStage.UNKNOWN,
        )
        assert report.total_tests == 0
        assert len(report.issues) == 0

    def test_report_serialization(self):
        report = AnalysisReport(
            job_name="test-job",
            build_number=1,
            overall_status="FAILURE",
            failure_stage=PipelineStage.TESTING,
            issues=[
                AnalyzedIssue(
                    title="Test issue",
                    description="Something failed",
                    category=FailureCategory.FUNCTION_BUG,
                    severity=Severity.HIGH,
                    stage=PipelineStage.TESTING,
                ),
            ],
        )
        json_str = report.model_dump_json()
        assert "test-job" in json_str
        assert "function_bug" in json_str
