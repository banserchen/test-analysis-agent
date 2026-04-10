"""Main analysis agent - orchestrates the entire failure analysis pipeline.

This is the core entry point that coordinates:
1. Fetching Jenkins build logs
2. Identifying the failure stage
3. Parsing Allure reports (if available)
4. Running LLM analysis
5. Executing applicable skills
6. Generating the final report
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from test_analysis_agent.config import Settings, get_settings
from test_analysis_agent.llm_analyzer import LLMAnalyzer
from test_analysis_agent.models.schemas import (
    AnalysisReport,
    AnalysisRequest,
    AnalyzedIssue,
    FailureCategory,
    PipelineStage,
    PipelineStageResult,
    Severity,
    TestCaseFailure,
)
from test_analysis_agent.parsers.allure_parser import AllureReportParser
from test_analysis_agent.parsers.jenkins_parser import JenkinsParser, parse_log_text
from test_analysis_agent.skills import (
    AllureCaseFailureClassifierSkill,
    BugDraftGeneratorSkill,
    CodeStacktraceMapperSkill,
    EnvInstabilityDetectorSkill,
    JenkinsDownstreamTraceSkill,
    JenkinsLogRootCauseSkill,
    ReportSummaryGeneratorSkill,
    TestBootstrapFailureSkill,
)
from test_analysis_agent.skills.base import SkillRegistry
from test_analysis_agent.skills.log_pattern_skill import LogPatternAnalyzerSkill

logger = logging.getLogger(__name__)


class AnalysisAgent:
    """Main analysis agent that orchestrates the failure analysis pipeline."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._llm = LLMAnalyzer(self._settings)
        self._skill_registry = SkillRegistry()
        self._setup_skills()

    def _setup_skills(self) -> None:
        """Register built-in skills and load custom ones."""
        # Built-in skills
        self._skill_registry.register(LogPatternAnalyzerSkill())
        self._skill_registry.register(JenkinsLogRootCauseSkill())
        self._skill_registry.register(JenkinsDownstreamTraceSkill())
        self._skill_registry.register(TestBootstrapFailureSkill())
        self._skill_registry.register(AllureCaseFailureClassifierSkill())
        self._skill_registry.register(CodeStacktraceMapperSkill())
        self._skill_registry.register(EnvInstabilityDetectorSkill())
        self._skill_registry.register(BugDraftGeneratorSkill())
        self._skill_registry.register(ReportSummaryGeneratorSkill())

        # Load custom skills from directory
        if self._settings.skills_dir:
            self._skill_registry.load_from_directory(self._settings.skills_dir)

    @property
    def skills(self) -> SkillRegistry:
        """Access the skill registry."""
        return self._skill_registry

    def analyze(self, request: AnalysisRequest) -> AnalysisReport:
        """Run the complete analysis pipeline.

        This is the main entry point for analyzing a pipeline failure.
        """
        logger.info("Starting analysis for %s #%d", request.job_name, request.build_number)

        # Step 1: Fetch Jenkins build log
        log_text, build_info = self._fetch_build_data(request)

        # Step 2: Parse log to identify stages
        stage_results = parse_log_text(log_text)

        # Step 3: Determine failure stage
        failure_stage = self._determine_failure_stage(stage_results)

        # Step 4: Run skill-based analysis
        skill_context = {"log_text": log_text, "stage_results": stage_results, "request": request}
        skill_results = self._skill_registry.execute_applicable(skill_context)

        # Step 5: Handle triggered job failures
        self._analyze_triggered_jobs(request, stage_results)

        # Step 6: Analyze based on failure stage
        issues: list[AnalyzedIssue] = []
        test_failures: list[TestCaseFailure] = []
        test_stats = {"total": 0, "passed": 0, "failed": 0, "broken": 0, "skipped": 0}

        if failure_stage in (PipelineStage.PREPARATION, PipelineStage.DEPLOYMENT, PipelineStage.UNKNOWN):
            # Analyze pipeline/deployment failures via LLM
            issues = self._analyze_stage_failures(stage_results)
        elif failure_stage == PipelineStage.TESTING:
            # Check if tests actually ran by looking for Allure data
            allure_data = self._parse_allure_report(request)
            if allure_data and allure_data.has_test_results():
                # Tests ran - analyze Allure report
                test_stats = allure_data.statistics
                test_failures = allure_data.get_failures()

                # Analyze individual test failures with LLM
                test_failures = self._analyze_test_failures(test_failures, request)

                # Batch analyze for issue grouping
                issues = self._llm.analyze_test_failures_batch(test_failures)
            else:
                # Tests didn't run - analyze as a stage failure
                issues = self._analyze_stage_failures(stage_results)
                # Add a specific issue about test startup failure
                issues.append(
                    AnalyzedIssue(
                        title="Test execution did not start",
                        description="The testing stage failed before any tests could be executed. "
                        "This is likely a test infrastructure or configuration issue.",
                        category=FailureCategory.TEST_STARTUP_FAILURE,
                        severity=Severity.HIGH,
                        stage=PipelineStage.TESTING,
                        root_cause="Tests failed to start - check test runner configuration and dependencies",
                        suggestion="Review the test runner logs for startup errors",
                        should_file_bug=False,
                    )
                )

        # Step 7: Enrich issues with skill findings
        issues = self._enrich_with_skill_findings(issues, skill_results)

        # Step 8: Generate summary
        summary, bug_recs = self._llm.generate_summary(
            job_name=request.job_name,
            build_number=request.build_number,
            failure_stage=failure_stage,
            issues=issues,
            total_tests=test_stats.get("total", 0),
            passed_tests=test_stats.get("passed", 0),
            failed_tests=test_stats.get("failed", 0),
            broken_tests=test_stats.get("broken", 0),
        )

        # Step 9: Build the report
        build_url = None
        if self._settings.jenkins_url:
            jenkins = JenkinsParser(
                self._settings.jenkins_url, self._settings.jenkins_username, self._settings.jenkins_password
            )
            build_url = jenkins.get_build_url(request.job_name, request.build_number)

        report = AnalysisReport(
            job_name=request.job_name,
            build_number=request.build_number,
            build_url=build_url,
            overall_status=build_info.get("result", "FAILURE") if build_info else "FAILURE",
            failure_stage=failure_stage,
            stage_results=stage_results,
            total_tests=test_stats.get("total", 0),
            passed_tests=test_stats.get("passed", 0),
            failed_tests=test_stats.get("failed", 0),
            broken_tests=test_stats.get("broken", 0),
            skipped_tests=test_stats.get("skipped", 0),
            test_failures=test_failures,
            issues=issues,
            summary=summary,
            bug_recommendations=bug_recs,
        )

        # Step 10: Apply skill post-processing
        report = self._skill_registry.apply_post_processing(report)

        logger.info(
            "Analysis complete for %s #%d: %d issues found",
            request.job_name, request.build_number, len(issues),
        )
        return report

    def analyze_log_text(self, log_text: str, job_name: str = "unknown", build_number: int = 0) -> AnalysisReport:
        """Analyze a raw log text directly without Jenkins connection.

        Useful for testing or when logs are provided directly.
        """
        stage_results = parse_log_text(log_text)
        failure_stage = self._determine_failure_stage(stage_results)

        # Run skills
        skill_context = {"log_text": log_text, "stage_results": stage_results}
        skill_results = self._skill_registry.execute_applicable(skill_context)

        # Analyze stage failures
        issues = self._analyze_stage_failures(stage_results)
        issues = self._enrich_with_skill_findings(issues, skill_results)

        summary, bug_recs = self._llm.generate_summary(
            job_name=job_name,
            build_number=build_number,
            failure_stage=failure_stage,
            issues=issues,
        )

        report = AnalysisReport(
            job_name=job_name,
            build_number=build_number,
            overall_status="FAILURE",
            failure_stage=failure_stage,
            stage_results=stage_results,
            issues=issues,
            summary=summary,
            bug_recommendations=bug_recs,
        )

        return self._skill_registry.apply_post_processing(report)

    def _fetch_build_data(self, request: AnalysisRequest) -> tuple[str, dict]:
        """Fetch build log and info from Jenkins."""
        jenkins_url = request.jenkins_url or self._settings.jenkins_url
        if not jenkins_url:
            logger.warning("No Jenkins URL configured, cannot fetch build data")
            return "", {}

        try:
            parser = JenkinsParser(jenkins_url, self._settings.jenkins_username, self._settings.jenkins_password)
            log_text = parser.get_build_log(request.job_name, request.build_number)
            build_info = parser.get_build_info(request.job_name, request.build_number)
            return log_text, build_info
        except Exception as exc:
            logger.error("Failed to fetch build data: %s", exc)
            return "", {}

    def _determine_failure_stage(self, stage_results: list[PipelineStageResult]) -> PipelineStage:
        """Determine which stage caused the failure."""
        for result in reversed(stage_results):
            if result.status == "fail":
                return result.stage
        return PipelineStage.UNKNOWN

    def _analyze_triggered_jobs(self, request: AnalysisRequest, stage_results: list[PipelineStageResult]) -> None:
        """Analyze triggered job failures by fetching their logs."""
        jenkins_url = request.jenkins_url or self._settings.jenkins_url
        if not jenkins_url:
            return

        for stage in stage_results:
            for job in stage.triggered_jobs:
                if job.status in ("failure", "unstable"):
                    try:
                        parser = JenkinsParser(
                            jenkins_url, self._settings.jenkins_username, self._settings.jenkins_password
                        )
                        triggered_log = parser.get_triggered_job_log(job.job_name, job.build_number)
                        if triggered_log:
                            # Add triggered job errors to the stage's error summary
                            triggered_errors = "\n".join(
                                f"[{job.job_name}] {line}"
                                for result in parse_log_text(triggered_log)
                                if result.error_summary
                                for line in result.error_summary.splitlines()[:10]
                            )
                            if triggered_errors:
                                existing = stage.error_summary or ""
                                stage.error_summary = f"{existing}\n\nTriggered job errors:\n{triggered_errors}"
                    except Exception as exc:
                        logger.warning("Failed to analyze triggered job %s: %s", job.job_name, exc)

    def _parse_allure_report(self, request: AnalysisRequest) -> Optional[object]:
        """Parse Allure report if available."""
        if request.allure_report_path or request.allure_report_url:
            try:
                parser = AllureReportParser(
                    report_path=request.allure_report_path,
                    report_url=request.allure_report_url,
                )
                return parser.parse()
            except Exception as exc:
                logger.error("Failed to parse Allure report: %s", exc)
        return None

    def _analyze_stage_failures(self, stage_results: list[PipelineStageResult]) -> list[AnalyzedIssue]:
        """Analyze pipeline stage failures using LLM."""
        issues: list[AnalyzedIssue] = []
        for stage in stage_results:
            if stage.status == "fail":
                issue = self._llm.analyze_stage_failure(stage)
                if issue:
                    issues.append(issue)
        return issues

    def _analyze_test_failures(
        self,
        failures: list[TestCaseFailure],
        request: AnalysisRequest,
    ) -> list[TestCaseFailure]:
        """Analyze individual test failures with LLM, optionally including test code."""
        analyzed: list[TestCaseFailure] = []
        for failure in failures:
            test_code = self._find_test_code(failure, request)
            analyzed_failure = self._llm.analyze_test_failure(failure, test_code)
            analyzed.append(analyzed_failure)
        return analyzed

    def _find_test_code(self, failure: TestCaseFailure, request: AnalysisRequest) -> Optional[str]:
        """Try to find the source code for a failed test case."""
        test_path = request.test_repo_path
        if not test_path:
            return None

        base = Path(test_path).resolve()
        # Validate the test_repo_path exists and is a directory
        if not base.is_dir():
            return None

        # Try to locate the test file based on test class name
        test_class = failure.test_class or failure.test_name
        if not test_class:
            return None

        # Convert class name to potential file paths (using resolved base for safety)
        possible_paths = _class_to_paths(test_class, str(base))
        for path in possible_paths:
            # Ensure resolved path stays within the base directory
            try:
                resolved = path.resolve()
                resolved.relative_to(base)
            except (ValueError, OSError):
                continue
            if resolved.is_file():
                try:
                    content = resolved.read_text(encoding="utf-8")
                    # Try to extract just the relevant test method
                    method_name = failure.test_name.split(".")[-1] if "." in failure.test_name else failure.test_name
                    excerpt = _extract_method(content, method_name)
                    return excerpt or content[:3000]
                except OSError:
                    continue
        return None

    def _enrich_with_skill_findings(
        self,
        issues: list[AnalyzedIssue],
        skill_results: list[dict],
    ) -> list[AnalyzedIssue]:
        """Enrich analysis issues with findings from skills."""
        for result in skill_results:
            if result.get("_error"):
                continue
            findings = result.get("pattern_findings", [])
            for finding in findings:
                # Check if this finding adds new information not already in issues
                category = finding.get("category", "unknown")
                already_covered = any(i.category.value == category for i in issues)
                if not already_covered:
                    issues.append(
                        AnalyzedIssue(
                            title=finding.get("description", "Pattern match"),
                            description=f"Detected by {result.get('_skill_name', 'skill')}: "
                            f"{finding.get('description', '')} "
                            f"({finding.get('match_count', '0')} occurrences)",
                            category=_safe_failure_category(category),
                            severity=Severity.MEDIUM,
                            stage=PipelineStage.UNKNOWN,
                            should_file_bug=False,
                        )
                    )
        return issues


def _safe_failure_category(value: str) -> FailureCategory:
    """Safely convert string to FailureCategory, falling back to UNKNOWN."""
    try:
        return FailureCategory(value)
    except ValueError:
        return FailureCategory.UNKNOWN


def _class_to_paths(class_name: str, base_path: str) -> list[Path]:
    """Convert a test class name to possible file paths."""
    base = Path(base_path)
    parts = class_name.replace("::", ".").split(".")

    paths: list[Path] = []

    # Python style: com.example.TestClass -> com/example/test_class.py
    if len(parts) > 1:
        # Try direct path
        file_parts = parts[:-1]  # Remove class name
        file_name = parts[-1]

        # Python convention
        py_path = base / "/".join(file_parts) / f"{_camel_to_snake(file_name)}.py"
        paths.append(py_path)
        py_path2 = base / "/".join(file_parts[:-1]) / f"{_camel_to_snake(file_parts[-1])}.py"
        paths.append(py_path2)

        # Java/Kotlin convention
        java_path = base / "src/test/java" / "/".join(file_parts) / f"{file_name}.java"
        paths.append(java_path)
        kt_path = base / "src/test/kotlin" / "/".join(file_parts) / f"{file_name}.kt"
        paths.append(kt_path)

    # Try glob for partial match (sanitize to prevent glob injection)
    if parts:
        last_part = re.sub(r"[^\w]", "", parts[-1])  # Keep only alphanumeric/underscore
        if last_part:
            for ext in ["py", "java", "kt", "js", "ts"]:
                paths.extend(base.rglob(f"*{last_part}*.{ext}"))

    return paths


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    import re

    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _extract_method(source: str, method_name: str) -> Optional[str]:
    """Extract a method/function from source code by name."""
    import re

    # Python: def method_name(...)
    pattern = rf"((?:^[ \t]*@\w+.*\n)*^[ \t]*def\s+{re.escape(method_name)}\s*\(.*?\n(?:(?:^[ \t]+.+|^\s*)\n)*)"
    match = re.search(pattern, source, re.MULTILINE)
    if match:
        return match.group(0).rstrip()

    # Java/Kotlin: void/fun method_name(...)
    java_prefix = r"((?:^[ \t]*@\w+.*\n)*^[ \t]*(?:public|private|protected|fun|void|static|\w+)\s+.*?"
    java_suffix = rf"{re.escape(method_name)}\s*\(.*?\{{[\s\S]*?\n[ \t]*\}})"
    pattern = java_prefix + java_suffix
    match = re.search(pattern, source, re.MULTILINE)
    if match:
        return match.group(0).rstrip()

    return None
