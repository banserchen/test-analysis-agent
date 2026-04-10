"""Data models for pipeline failure analysis."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class PipelineStage(str, enum.Enum):
    """Stages of a CD pipeline."""

    PREPARATION = "preparation"
    DEPLOYMENT = "deployment"
    TESTING = "testing"
    UNKNOWN = "unknown"


class FailureCategory(str, enum.Enum):
    """Categories of failures detected during analysis."""

    ENVIRONMENT_ERROR = "environment_error"
    DEPENDENCY_ERROR = "dependency_error"
    DEPLOYMENT_ERROR = "deployment_error"
    CONFIGURATION_ERROR = "configuration_error"
    NETWORK_ERROR = "network_error"
    PERMISSION_ERROR = "permission_error"
    TEST_STARTUP_FAILURE = "test_startup_failure"
    TEST_CASE_FAILURE = "test_case_failure"
    TEST_INFRASTRUCTURE_ERROR = "test_infrastructure_error"
    TIMEOUT_ERROR = "timeout_error"
    RESOURCE_ERROR = "resource_error"
    CODE_BUG = "code_bug"
    UNKNOWN = "unknown"


class Severity(str, enum.Enum):
    """Severity levels for identified issues."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AnalysisRequest(BaseModel):
    """Request to analyze a pipeline failure."""

    jenkins_url: Optional[str] = Field(default=None, description="Jenkins server URL (overrides config)")
    job_name: str = Field(description="Full Jenkins job name (e.g., folder/job-name)")
    build_number: int = Field(description="Jenkins build number to analyze")
    allure_report_url: Optional[str] = Field(default=None, description="URL to Allure report (if available)")
    allure_report_path: Optional[str] = Field(default=None, description="Local path to Allure report directory")
    test_repo_url: Optional[str] = Field(default=None, description="URL of the test source code repository")
    test_repo_path: Optional[str] = Field(default=None, description="Local path to the test source code")
    additional_context: Optional[str] = Field(default=None, description="Additional context about the pipeline")


class TriggeredJobInfo(BaseModel):
    """Information about a triggered downstream job."""

    job_name: str
    build_number: int
    status: str
    url: Optional[str] = None


class PipelineStageResult(BaseModel):
    """Result of analyzing a single pipeline stage."""

    stage: PipelineStage
    status: str = Field(description="pass, fail, skipped, or unknown")
    error_summary: Optional[str] = None
    log_excerpt: Optional[str] = None
    triggered_jobs: list[TriggeredJobInfo] = Field(default_factory=list)


class TestCaseFailure(BaseModel):
    """Details of a single test case failure."""

    test_name: str = Field(description="Full test case name including class/suite")
    test_class: Optional[str] = None
    suite_name: Optional[str] = None
    status: str = Field(description="failed, broken, or unknown")
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    duration_ms: Optional[float] = None
    categories: list[str] = Field(default_factory=list, description="Allure categories/defect types")
    ai_analysis: Optional[str] = Field(default=None, description="AI-generated analysis of this failure")
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    related_code: Optional[str] = Field(default=None, description="Related test code snippet")


class AnalyzedIssue(BaseModel):
    """A single analyzed issue from the pipeline."""

    title: str = Field(description="Short title describing the issue")
    description: str = Field(description="Detailed description of the issue")
    category: FailureCategory
    severity: Severity
    stage: PipelineStage
    affected_tests: list[str] = Field(default_factory=list, description="Test names affected by this issue")
    root_cause: Optional[str] = Field(default=None, description="Root cause analysis")
    suggestion: Optional[str] = Field(default=None, description="Suggested fix or next step")
    should_file_bug: bool = Field(default=False, description="Whether a bug should be filed for this issue")
    bug_summary: Optional[str] = Field(default=None, description="Suggested bug report summary")
    log_evidence: Optional[str] = Field(default=None, description="Log excerpt showing evidence")


class AnalysisReport(BaseModel):
    """Complete analysis report for a pipeline failure."""

    job_name: str
    build_number: int
    build_url: Optional[str] = None
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    overall_status: str = Field(description="Overall pipeline status")
    failure_stage: PipelineStage = Field(description="Stage where failure occurred")

    # Stage results
    stage_results: list[PipelineStageResult] = Field(default_factory=list)

    # Test results (if testing stage was reached)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    broken_tests: int = 0
    skipped_tests: int = 0
    test_failures: list[TestCaseFailure] = Field(default_factory=list)

    # Analyzed issues
    issues: list[AnalyzedIssue] = Field(default_factory=list)

    # Summary
    summary: str = Field(default="", description="AI-generated executive summary")
    bug_recommendations: list[str] = Field(default_factory=list, description="Bug filing recommendations")
