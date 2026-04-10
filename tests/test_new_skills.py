"""Tests for all 8 new skill packages."""

from test_analysis_agent.models.schemas import (
    AnalysisReport,
    AnalyzedIssue,
    FailureCategory,
    PipelineStage,
    PipelineStageResult,
    Severity,
    TriggeredJobInfo,
)
from test_analysis_agent.skills.allure_case_failure_classifier import AllureCaseFailureClassifierSkill
from test_analysis_agent.skills.allure_case_failure_classifier.postprocess import (
    validate_output as validate_classifier,
)
from test_analysis_agent.skills.bug_draft_generator import BugDraftGeneratorSkill
from test_analysis_agent.skills.bug_draft_generator.postprocess import validate_output as validate_bug
from test_analysis_agent.skills.code_stacktrace_mapper import CodeStacktraceMapperSkill
from test_analysis_agent.skills.code_stacktrace_mapper.postprocess import validate_output as validate_mapper
from test_analysis_agent.skills.env_instability_detector import EnvInstabilityDetectorSkill
from test_analysis_agent.skills.env_instability_detector.postprocess import validate_output as validate_env
from test_analysis_agent.skills.jenkins_downstream_trace import JenkinsDownstreamTraceSkill
from test_analysis_agent.skills.jenkins_downstream_trace.postprocess import validate_output as validate_trace
from test_analysis_agent.skills.jenkins_log_root_cause import JenkinsLogRootCauseSkill
from test_analysis_agent.skills.jenkins_log_root_cause.postprocess import validate_output as validate_root_cause
from test_analysis_agent.skills.report_summary_generator import ReportSummaryGeneratorSkill
from test_analysis_agent.skills.report_summary_generator.postprocess import validate_output as validate_report
from test_analysis_agent.skills.test_bootstrap_failure import TestBootstrapFailureSkill
from test_analysis_agent.skills.test_bootstrap_failure.postprocess import validate_output as validate_bootstrap

# =====================================================================
# 1. skill.jenkins.log_root_cause
# =====================================================================


class TestJenkinsLogRootCause:
    def test_can_handle(self):
        skill = JenkinsLogRootCauseSkill()
        assert skill.can_handle({"log_text": "some log"}) is True
        assert skill.can_handle({}) is False
        assert skill.can_handle({"log_text": ""}) is False

    def test_detect_error_in_log(self):
        skill = JenkinsLogRootCauseSkill()
        log = "INFO: Building...\nERROR: Compilation failed\nFATAL: Build aborted"
        result = skill.execute({"log_text": log})
        assert len(result["root_cause_snippets"]) > 0
        assert result["primary_error"]

    def test_no_errors(self):
        skill = JenkinsLogRootCauseSkill()
        result = skill.execute({"log_text": "INFO: Success\nINFO: Done"})
        assert len(result["root_cause_snippets"]) == 0
        assert result["primary_error"] == "No clear error detected"

    def test_confidence_ordering(self):
        skill = JenkinsLogRootCauseSkill()
        log = "ModuleNotFoundError: No module named 'foo'\n" + "x\n" * 100 + "Connection refused"
        result = skill.execute({"log_text": log})
        snippets = result["root_cause_snippets"]
        if len(snippets) > 1:
            # First snippet should have higher or equal confidence
            assert snippets[0]["confidence"] >= snippets[-1]["confidence"]

    def test_postprocess_valid(self):
        data = {
            "root_cause_snippets": [
                {"line_start": 1, "line_end": 5, "text": "error text", "error_type": "compilation", "confidence": 0.8}
            ],
            "primary_error": "Compilation failed",
        }
        is_valid, errors = validate_root_cause(data)
        assert is_valid, errors

    def test_postprocess_invalid(self):
        is_valid, errors = validate_root_cause({"root_cause_snippets": "not a list"})
        assert not is_valid
        assert len(errors) > 0


# =====================================================================
# 2. skill.jenkins.downstream_trace
# =====================================================================


class TestJenkinsDownstreamTrace:
    def test_can_handle_with_trigger(self):
        skill = JenkinsDownstreamTraceSkill()
        log = "Starting building: deploy/staging #42"
        assert skill.can_handle({"log_text": log}) is True

    def test_can_handle_without_trigger(self):
        skill = JenkinsDownstreamTraceSkill()
        assert skill.can_handle({"log_text": "INFO: normal log"}) is False

    def test_extract_triggered_jobs(self):
        skill = JenkinsDownstreamTraceSkill()
        log = (
            "Starting building: deploy/staging #42\n"
            "deploy/staging #42 completed with result FAILURE\n"
            "Starting building: test/integration #99\n"
            "test/integration #99 completed with result SUCCESS\n"
        )
        result = skill.execute({"log_text": log})
        assert len(result["call_chain"]) == 2
        assert "deploy/staging" in result["failing_chain"]
        assert "test/integration" not in result["failing_chain"]
        assert result["summary"]

    def test_no_triggered_jobs(self):
        skill = JenkinsDownstreamTraceSkill()
        # Need "Triggering" in can_handle, but no actual patterns
        result = skill.execute({"log_text": "Triggering but no real match here"})
        assert len(result["call_chain"]) == 0
        assert result["summary"]

    def test_with_stage_results(self):
        skill = JenkinsDownstreamTraceSkill()
        stage = PipelineStageResult(
            stage=PipelineStage.DEPLOYMENT,
            status="fail",
            triggered_jobs=[
                TriggeredJobInfo(job_name="child/job", build_number=10, status="failure"),
            ],
        )
        result = skill.execute({
            "log_text": "Starting building: child/job #10",
            "stage_results": [stage],
        })
        chain_names = [e["job_name"] for e in result["call_chain"]]
        assert "child/job" in chain_names

    def test_postprocess_valid(self):
        data = {
            "call_chain": [{"job_name": "a", "build_number": 1, "status": "failure", "trigger_line": 1}],
            "failing_chain": ["a"],
            "summary": "Job a failed",
        }
        is_valid, errors = validate_trace(data)
        assert is_valid, errors


# =====================================================================
# 3. skill.test.bootstrap_failure
# =====================================================================


class TestBootstrapFailure:
    def test_can_handle_pip(self):
        skill = TestBootstrapFailureSkill()
        assert skill.can_handle({"log_text": "pip install -r requirements.txt"}) is True

    def test_can_handle_no_match(self):
        skill = TestBootstrapFailureSkill()
        assert skill.can_handle({"log_text": "all 50 tests passed"}) is False

    def test_detect_pip_failure(self):
        skill = TestBootstrapFailureSkill()
        log = "Running pip install -r requirements.txt\nERROR: Could not find a version"
        result = skill.execute({"log_text": log})
        assert result["is_bootstrap_failure"] is True
        assert result["bootstrap_phase"] == "pip_install"

    def test_detect_collection_error(self):
        skill = TestBootstrapFailureSkill()
        log = "collected 0 items / 1 error\nE ModuleNotFoundError: No module named 'myapp'"
        result = skill.execute({"log_text": log})
        assert result["is_bootstrap_failure"] is True
        assert result["bootstrap_phase"] == "pytest_collection"

    def test_detect_pytest_startup(self):
        skill = TestBootstrapFailureSkill()
        log = "INTERNALERROR> Traceback\nINTERNALERROR> conftest.py error"
        result = skill.execute({"log_text": log})
        assert result["is_bootstrap_failure"] is True
        assert result["bootstrap_phase"] == "pytest_startup"

    def test_no_bootstrap_failure(self):
        skill = TestBootstrapFailureSkill()
        log = "pip install completed\npytest all passed"
        result = skill.execute({"log_text": log})
        assert result["is_bootstrap_failure"] is False

    def test_postprocess_valid(self):
        data = {
            "is_bootstrap_failure": True,
            "bootstrap_phase": "pip_install",
            "error_details": "some error",
            "suggestion": "fix it",
        }
        is_valid, errors = validate_bootstrap(data)
        assert is_valid, errors


# =====================================================================
# 4. skill.allure.case_failure_classifier
# =====================================================================


class TestAllureCaseFailureClassifier:
    def test_can_handle(self):
        skill = AllureCaseFailureClassifierSkill()
        assert skill.can_handle({"test_failures": [{"test_name": "t"}]}) is True
        assert skill.can_handle({"test_failures": []}) is False
        assert skill.can_handle({}) is False

    def test_classify_product_bug(self):
        skill = AllureCaseFailureClassifierSkill()
        result = skill.execute({
            "test_failures": [{
                "test_name": "test_api_response",
                "error_message": "HTTP 500 Internal Server Error",
                "stack_trace": "",
                "categories": [],
            }]
        })
        assert result["classifications"][0]["failure_class"] == "product_bug"

    def test_classify_infrastructure(self):
        skill = AllureCaseFailureClassifierSkill()
        result = skill.execute({
            "test_failures": [{
                "test_name": "test_deploy",
                "error_message": "docker container failed to start",
                "stack_trace": "",
                "categories": [],
            }]
        })
        assert result["classifications"][0]["failure_class"] == "infrastructure"

    def test_classify_from_allure_category(self):
        skill = AllureCaseFailureClassifierSkill()
        result = skill.execute({
            "test_failures": [{
                "test_name": "test_flaky_network",
                "error_message": "random timeout",
                "stack_trace": "",
                "categories": ["Flaky tests"],
            }]
        })
        assert result["classifications"][0]["failure_class"] == "flaky"

    def test_summary_counts(self):
        skill = AllureCaseFailureClassifierSkill()
        result = skill.execute({
            "test_failures": [
                {"test_name": "t1", "error_message": "HTTP 500", "stack_trace": "", "categories": []},
                {"test_name": "t2", "error_message": "docker fail", "stack_trace": "", "categories": []},
            ]
        })
        assert result["summary"]["total"] == 2

    def test_postprocess_valid(self):
        data = {
            "classifications": [
                {"test_name": "t1", "failure_class": "product_bug", "reason": "matched", "confidence": 0.8}
            ],
            "summary": {"total": 1, "by_class": {"product_bug": 1}},
        }
        is_valid, errors = validate_classifier(data)
        assert is_valid, errors


# =====================================================================
# 5. skill.code.stacktrace_mapper
# =====================================================================


class TestCodeStacktraceMapper:
    def test_can_handle_direct(self):
        skill = CodeStacktraceMapperSkill()
        assert skill.can_handle({"stack_trace": "File 'x.py', line 1"}) is True
        assert skill.can_handle({}) is False

    def test_can_handle_from_failures(self):
        skill = CodeStacktraceMapperSkill()
        assert skill.can_handle({"test_failures": [{"stack_trace": "some trace"}]}) is True

    def test_parse_python_trace(self):
        skill = CodeStacktraceMapperSkill()
        trace = (
            'Traceback (most recent call last):\n'
            '  File "/app/src/main.py", line 42, in run\n'
            '    result = process(data)\n'
            '  File "/usr/lib/python3.12/json/__init__.py", line 100, in loads\n'
            '    raise JSONDecodeError\n'
        )
        result = skill.execute({"stack_trace": trace})
        assert result["language"] == "python"
        assert len(result["frames"]) == 2
        # /app/src/main.py is user code
        assert result["frames"][0]["is_user_code"] is True
        # stdlib is not user code
        assert result["frames"][1]["is_user_code"] is False
        assert result["deepest_user_frame"]["file"] == "/app/src/main.py"

    def test_parse_java_trace(self):
        skill = CodeStacktraceMapperSkill()
        trace = (
            "java.lang.NullPointerException\n"
            "\tat com.example.MyService.process(MyService.java:55)\n"
            "\tat java.lang.Thread.run(Thread.java:750)\n"
        )
        result = skill.execute({"stack_trace": trace})
        assert result["language"] == "java"
        assert len(result["frames"]) == 2
        assert result["frames"][0]["is_user_code"] is True
        assert result["frames"][1]["is_user_code"] is False

    def test_parse_js_trace(self):
        skill = CodeStacktraceMapperSkill()
        trace = (
            "Error: ECONNREFUSED\n"
            "    at TCPConnectWrap.afterConnect (/app/server.js:10:5)\n"
            "    at Module._compile (node_modules/module.js:20:10)\n"
        )
        result = skill.execute({"stack_trace": trace})
        assert result["language"] == "javascript"
        assert len(result["frames"]) == 2
        assert result["frames"][0]["is_user_code"] is True
        assert result["frames"][1]["is_user_code"] is False

    def test_empty_trace(self):
        skill = CodeStacktraceMapperSkill()
        result = skill.execute({"stack_trace": ""})
        assert result["frames"] == []
        assert result["deepest_user_frame"] is None

    def test_postprocess_valid(self):
        data = {
            "frames": [
                {"file": "x.py", "line": 1, "function": "f", "code_snippet": "", "is_user_code": True}
            ],
            "language": "python",
            "deepest_user_frame": {"file": "x.py", "line": 1, "function": "f"},
        }
        is_valid, errors = validate_mapper(data)
        assert is_valid, errors


# =====================================================================
# 6. skill.env.instability_detector
# =====================================================================


class TestEnvInstabilityDetector:
    def test_can_handle(self):
        skill = EnvInstabilityDetectorSkill()
        assert skill.can_handle({"log_text": "log"}) is True
        assert skill.can_handle({}) is False

    def test_detect_network(self):
        skill = EnvInstabilityDetectorSkill()
        result = skill.execute({"log_text": "Connection reset by peer\nRetrying..."})
        assert result["is_unstable"] is True
        instabilities = result["instabilities"]
        assert any(i["category"] == "network" for i in instabilities)

    def test_detect_dns(self):
        skill = EnvInstabilityDetectorSkill()
        result = skill.execute({"log_text": "Temporary failure in name resolution"})
        assert result["is_unstable"] is True
        assert any(i["category"] == "dns" for i in result["instabilities"])

    def test_detect_dependency_source(self):
        skill = EnvInstabilityDetectorSkill()
        result = skill.execute({"log_text": "pypi.org 503 Service Unavailable timeout"})
        assert result["is_unstable"] is True
        assert any(i["category"] == "dependency_source" for i in result["instabilities"])

    def test_detect_disk(self):
        skill = EnvInstabilityDetectorSkill()
        result = skill.execute({"log_text": "No space left on device"})
        assert any(i["category"] == "disk" for i in result["instabilities"])

    def test_detect_certificate(self):
        skill = EnvInstabilityDetectorSkill()
        result = skill.execute({"log_text": "SSL certificate verify failed"})
        assert any(i["category"] == "certificate" for i in result["instabilities"])

    def test_stable(self):
        skill = EnvInstabilityDetectorSkill()
        result = skill.execute({"log_text": "INFO: Everything OK"})
        assert result["is_unstable"] is False
        assert len(result["instabilities"]) == 0

    def test_postprocess_valid(self):
        data = {
            "instabilities": [
                {"category": "network", "evidence": "conn reset", "occurrence_count": 1, "severity": "high"}
            ],
            "is_unstable": True,
            "summary": "Network instability detected",
        }
        is_valid, errors = validate_env(data)
        assert is_valid, errors


# =====================================================================
# 7. skill.bug.draft_generator
# =====================================================================


class TestBugDraftGenerator:
    def test_can_handle(self):
        skill = BugDraftGeneratorSkill()
        assert skill.can_handle({"issues": [{"title": "bug"}]}) is True
        assert skill.can_handle({"issues": []}) is False
        assert skill.can_handle({}) is False

    def test_generate_draft_for_critical(self):
        skill = BugDraftGeneratorSkill()
        result = skill.execute({
            "issues": [{
                "title": "Database connection failure",
                "description": "DB is down",
                "category": "network_error",
                "severity": "critical",
                "root_cause": "DB server unreachable",
                "suggestion": "Check DB health",
                "should_file_bug": True,
                "affected_tests": ["test_db_query"],
                "log_evidence": "Connection refused to port 5432",
            }],
            "job_name": "my-job",
            "build_number": 99,
        })
        assert result["count"] == 1
        draft = result["bug_drafts"][0]
        assert "CRITICAL" in draft["title"]
        assert draft["severity"] == "critical"
        assert "severity:critical" in draft["labels"]

    def test_skip_low_severity(self):
        skill = BugDraftGeneratorSkill()
        result = skill.execute({
            "issues": [{
                "title": "Minor warning",
                "description": "Not important",
                "category": "unknown",
                "severity": "low",
                "should_file_bug": False,
                "root_cause": "",
                "suggestion": "",
                "affected_tests": [],
                "log_evidence": "",
            }],
        })
        assert result["count"] == 0

    def test_with_analyzed_issue_objects(self):
        skill = BugDraftGeneratorSkill()
        issue = AnalyzedIssue(
            title="Deploy failed",
            description="Helm chart not found",
            category=FailureCategory.DEPLOYMENT_ERROR,
            severity=Severity.HIGH,
            stage=PipelineStage.DEPLOYMENT,
            should_file_bug=True,
            bug_summary="Helm chart missing",
        )
        result = skill.execute({"issues": [issue], "job_name": "deploy", "build_number": 5})
        assert result["count"] == 1

    def test_postprocess_valid(self):
        data = {
            "bug_drafts": [{
                "title": "Bug",
                "severity": "high",
                "component": "Product",
                "description": "Desc",
                "steps_to_reproduce": "...",
                "expected_behavior": "...",
                "actual_behavior": "...",
                "environment": "...",
                "labels": ["severity:high"],
            }],
            "count": 1,
        }
        is_valid, errors = validate_bug(data)
        assert is_valid, errors


# =====================================================================
# 8. skill.report.summary_generator
# =====================================================================


class TestReportSummaryGenerator:
    def test_can_handle(self):
        skill = ReportSummaryGeneratorSkill()
        report = AnalysisReport(
            job_name="test",
            build_number=1,
            overall_status="FAILURE",
            failure_stage=PipelineStage.TESTING,
        )
        assert skill.can_handle({"report": report}) is True
        assert skill.can_handle({"issues": [{"title": "x"}]}) is True
        assert skill.can_handle({}) is False

    def test_generate_from_report(self):
        skill = ReportSummaryGeneratorSkill()
        report = AnalysisReport(
            job_name="my-app/deploy",
            build_number=42,
            overall_status="FAILURE",
            failure_stage=PipelineStage.TESTING,
            total_tests=100,
            passed_tests=90,
            failed_tests=10,
            issues=[
                AnalyzedIssue(
                    title="API regression",
                    description="API returns 500",
                    category=FailureCategory.CODE_BUG,
                    severity=Severity.CRITICAL,
                    stage=PipelineStage.TESTING,
                    root_cause="Null pointer",
                    suggestion="Fix null check",
                    should_file_bug=True,
                    bug_summary="Fix API null pointer",
                )
            ],
            bug_recommendations=["File bug for API regression"],
        )
        result = skill.execute({"report": report})
        mgmt = result["management_summary"]
        dev = result["developer_report"]

        assert mgmt["risk_level"] == "critical"
        assert mgmt["status_emoji"] == "🔴"
        assert mgmt["key_metrics"]["total_tests"] == 100
        assert mgmt["key_metrics"]["pass_rate"] == "90.0%"
        assert len(mgmt["action_items"]) > 0

        assert dev["title"]
        assert len(dev["failure_breakdown"]) > 0
        assert dev["detailed_findings"]

    def test_generate_from_issues_fallback(self):
        skill = ReportSummaryGeneratorSkill()
        result = skill.execute({
            "issues": [{"title": "x", "severity": "high", "suggestion": "fix"}],
            "job_name": "job",
            "build_number": 1,
        })
        assert result["management_summary"]["risk_level"] == "high"

    def test_low_risk(self):
        skill = ReportSummaryGeneratorSkill()
        report = AnalysisReport(
            job_name="ok",
            build_number=1,
            overall_status="SUCCESS",
            failure_stage=PipelineStage.UNKNOWN,
        )
        result = skill.execute({"report": report})
        assert result["management_summary"]["risk_level"] == "low"
        assert result["management_summary"]["status_emoji"] == "🟢"

    def test_postprocess_valid(self):
        data = {
            "management_summary": {
                "title": "Report",
                "status_emoji": "🔴",
                "one_liner": "Failed",
                "key_metrics": {"total_tests": 0, "pass_rate": "N/A", "critical_issues": 0, "bug_recommendations": 0},
                "risk_level": "high",
                "action_items": [],
                "body": "Summary text",
            },
            "developer_report": {
                "title": "Dev Report",
                "failure_breakdown": [],
                "detailed_findings": "Details here",
                "next_steps": [],
            },
        }
        is_valid, errors = validate_report(data)
        assert is_valid, errors

    def test_postprocess_invalid(self):
        is_valid, errors = validate_report({"management_summary": "not an object"})
        assert not is_valid
