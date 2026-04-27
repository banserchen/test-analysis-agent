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

    def test_stage_name_preserved(self):
        """stage_name must carry the actual Jenkins stage label, not enum value."""
        log = """[Pipeline] { (#1 准备工作)
Everything fine
[Pipeline] { (#2 部署服务)
ERROR: deploy failed with exit code 1"""

        results = parse_log_text(log)
        names = [r.stage_name for r in results]
        assert "#1 准备工作" in names
        assert "#2 部署服务" in names

    def test_numbered_stages_grouped_by_major(self):
        """All #3.x sub-stages must be grouped into one block."""
        log = """[Pipeline] { (#3.0 切换测试环境)
OK
[Pipeline] { (#3.1 准备ansible)
OK
[Pipeline] { (#3.2 仿真车部署)
ERROR: container failed"""

        results = parse_log_text(log)
        # All #3.x go into one block
        major3 = [r for r in results if r.stage_name.startswith("#3")]
        assert len(major3) == 1
        assert major3[0].status == "fail"
        # sub_stages should include all three
        assert any("3.2" in s for s in major3[0].sub_stages)

    def test_no_such_container_is_noise(self):
        """'No such container' Docker cleanup messages must not mark a stage as fail."""
        log = """[Pipeline] { (#1 清理)
Error response from daemon: No such container: test-auto
Cleanup complete"""

        results = parse_log_text(log)
        assert len(results) == 1
        assert results[0].status == "pass"

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
