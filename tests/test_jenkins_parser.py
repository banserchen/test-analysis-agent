"""Tests for Jenkins log parser."""

from test_analysis_agent.models.schemas import PipelineStage
from test_analysis_agent.parsers.jenkins_parser import (
    _detect_stage,
    _extract_error_lines,
    _extract_triggered_jobs,
    parse_log_text,
)


class TestDetectStage:
    """Tests for stage detection from log lines."""

    def test_detect_preparation_stage(self):
        assert _detect_stage("[Pipeline] { (Preparation)") == PipelineStage.PREPARATION
        assert _detect_stage("Stage: Environment Setup") is not None
        assert _detect_stage("checkout scm") == PipelineStage.PREPARATION

    def test_detect_deployment_stage(self):
        assert _detect_stage("[Pipeline] { (Deploy)") == PipelineStage.DEPLOYMENT
        assert _detect_stage("kubectl apply -f deployment.yaml") == PipelineStage.DEPLOYMENT
        assert _detect_stage("helm upgrade my-release chart/") == PipelineStage.DEPLOYMENT

    def test_detect_testing_stage(self):
        assert _detect_stage("[Pipeline] { (Test)") == PipelineStage.TESTING
        assert _detect_stage("pytest tests/") == PipelineStage.TESTING
        assert _detect_stage("allure generate report") == PipelineStage.TESTING

    def test_detect_no_stage(self):
        assert _detect_stage("Just a regular log line") is None
        assert _detect_stage("") is None


class TestExtractErrorLines:
    """Tests for error line extraction."""

    def test_extract_errors(self):
        log = """INFO: Starting build
ERROR: Build failed with exit code 1
WARNING: Something minor
FATAL: Cannot continue
Connection refused to server"""
        errors = _extract_error_lines(log)
        assert len(errors) == 3
        assert any("ERROR" in e for e in errors)
        assert any("FATAL" in e for e in errors)
        assert any("Connection refused" in e for e in errors)

    def test_no_errors(self):
        log = "INFO: All good\nSUCCESS: Build complete"
        errors = _extract_error_lines(log)
        assert len(errors) == 0


class TestExtractTriggeredJobs:
    """Tests for triggered job extraction."""

    def test_extract_triggered_job(self):
        log = """Starting build
Triggering a new build of job 'deploy-app' #42
deploy-app #42 completed with result SUCCESS"""
        jobs = _extract_triggered_jobs(log)
        assert len(jobs) == 1
        assert jobs[0].job_name == "deploy-app"
        assert jobs[0].build_number == 42
        assert jobs[0].status == "success"

    def test_extract_failed_triggered_job(self):
        log = """Triggering job 'test-suite' #10
job 'test-suite' #10 completed with result FAILURE"""
        jobs = _extract_triggered_jobs(log)
        assert len(jobs) == 1
        assert jobs[0].status == "failure"

    def test_no_triggered_jobs(self):
        log = "Just a regular build log\nNo triggered jobs here"
        jobs = _extract_triggered_jobs(log)
        assert len(jobs) == 0


class TestParseLogText:
    """Tests for full log parsing."""

    def test_parse_simple_failure(self):
        log = """[Pipeline] { (Preparation)
Checking out repository...
[Pipeline] { (Deploy)
kubectl apply -f deployment.yaml
ERROR: deployment failed with exit code 1
[Pipeline] { (Test)
pytest tests/
All tests passed"""

        results = parse_log_text(log)
        assert len(results) >= 2

        # Find the deploy stage
        deploy_stages = [r for r in results if r.stage == PipelineStage.DEPLOYMENT]
        assert len(deploy_stages) == 1
        assert deploy_stages[0].status == "fail"

    def test_parse_unknown_log(self):
        log = """Some random log output
ERROR: Something went wrong
More log output"""

        results = parse_log_text(log)
        assert len(results) == 1
        assert results[0].stage == PipelineStage.UNKNOWN
        assert results[0].status == "fail"

    def test_parse_clean_log(self):
        log = """[Pipeline] { (Preparation)
All dependencies installed
[Pipeline] { (Deploy)
Deployment successful
[Pipeline] { (Test)
All tests passed"""

        results = parse_log_text(log)
        assert all(r.status == "pass" for r in results)
