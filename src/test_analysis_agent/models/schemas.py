"""Data models for pipeline failure analysis."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class TokenUsage(BaseModel):
    """LLM token consumption for a single analysis run."""

    llm_model: str = Field(default="", description="LLM model used for this analysis")
    prompt_tokens: int = Field(default=0, description="Tokens consumed by prompts")
    completion_tokens: int = Field(default=0, description="Tokens consumed by completions")
    total_tokens: int = Field(default=0, description="Total tokens consumed")
    llm_calls: int = Field(default=0, description="Number of LLM API calls made")


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
    FUNCTION_BUG = "function_bug"
    UNKNOWN = "unknown"

    @property
    def label_zh(self) -> str:
        return _FAILURE_CATEGORY_ZH.get(self, self.value)


_FAILURE_CATEGORY_ZH: dict[FailureCategory, str] = {
    FailureCategory.ENVIRONMENT_ERROR: "环境错误",
    FailureCategory.DEPENDENCY_ERROR: "依赖错误",
    FailureCategory.DEPLOYMENT_ERROR: "部署错误",
    FailureCategory.CONFIGURATION_ERROR: "配置错误",
    FailureCategory.NETWORK_ERROR: "网络错误",
    FailureCategory.PERMISSION_ERROR: "权限错误",
    FailureCategory.TEST_STARTUP_FAILURE: "测试启动失败",
    FailureCategory.TEST_CASE_FAILURE: "测试用例问题",
    FailureCategory.TEST_INFRASTRUCTURE_ERROR: "测试基础设施故障",
    FailureCategory.TIMEOUT_ERROR: "超时错误",
    FailureCategory.RESOURCE_ERROR: "资源不足",
    FailureCategory.FUNCTION_BUG: "产品功能缺陷",
    FailureCategory.UNKNOWN: "未知",
}


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
    failing_task: Optional[str] = Field(
        default=None,
        description="Name of the ansible/shell TASK where this downstream job first failed",
    )
    root_cause_hint: Optional[str] = Field(
        default=None,
        description="Short human-readable hint explaining why this downstream job failed (ansible TASK name + fatal line)",
    )
    error_excerpt: Optional[str] = Field(
        default=None,
        description="Compact multi-line excerpt from the downstream log covering the failing TASK's context",
    )


class PipelineStageResult(BaseModel):
    """Result of analyzing a single pipeline stage."""

    # The actual stage name from the Jenkins log (e.g. "#3 部署", "#4 执行测试").
    stage_name: str = Field(default="", description="Actual Jenkins stage name as-is from the log")

    # Kept for internal logic only — excluded from JSON output.
    stage: PipelineStage = Field(default=PipelineStage.UNKNOWN, exclude=True)

    status: str = Field(description="pass, fail, skipped, or unknown")
    error_summary: Optional[str] = None
    log_excerpt: Optional[str] = None
    triggered_jobs: list[TriggeredJobInfo] = Field(default_factory=list)
    sub_stages: list[str] = Field(
        default_factory=list,
        description="All Jenkins sub-stage names within this block in entry order",
    )
    failing_sub_stage: Optional[str] = Field(
        default=None,
        description="The specific sub-stage where the first real error occurred",
    )


class TestCaseFailure(BaseModel):
    """Details of a single test case failure."""

    model_config = ConfigDict(validate_assignment=True)

    test_name: str= Field(description="Full test case name including class/suite")
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

    @field_validator("failure_category", mode="before")
    @classmethod
    def coerce_failure_category(cls, v: object) -> FailureCategory:
        if isinstance(v, FailureCategory):
            return v
        s = str(v)
        # backward compat: old "code_bug" value maps to function_bug
        if s == "code_bug":
            return FailureCategory.FUNCTION_BUG
        try:
            return FailureCategory(s)
        except ValueError:
            return FailureCategory.UNKNOWN

    @computed_field
    @property
    def failure_category_label(self) -> str:
        """失败分类的中文显示名。"""
        cat = self.failure_category
        if not isinstance(cat, FailureCategory):
            try:
                cat = FailureCategory(str(cat))
            except ValueError:
                return str(cat)
        return cat.label_zh


class AnalyzedIssue(BaseModel):
    """A single analyzed issue from the pipeline."""

    title: str = Field(description="Short title describing the issue")
    description: str = Field(description="Detailed description of the issue")
    category: FailureCategory
    severity: Severity
    # Actual stage name (e.g. "#3.2 仿真车部署") — preferred display field.
    stage_name: str = Field(default="", description="Actual Jenkins stage name where this issue occurred")
    # Kept for internal logic only — excluded from JSON output.
    stage: PipelineStage = Field(default=PipelineStage.UNKNOWN, exclude=True)
    affected_tests: list[str] = Field(default_factory=list, description="Test names affected by this issue")
    root_cause: Optional[str] = Field(default=None, description="Root cause analysis")
    suggestion: Optional[str] = Field(default=None, description="Suggested fix or next step")
    should_file_bug: bool = Field(default=False, description="Whether a bug should be filed for this issue")
    bug_summary: Optional[str] = Field(default=None, description="Suggested bug report summary")
    log_evidence: Optional[str] = Field(default=None, description="Log excerpt showing evidence")

    @field_validator("category", mode="before")
    @classmethod
    def coerce_category(cls, v: object) -> FailureCategory:
        if isinstance(v, FailureCategory):
            return v
        s = str(v)
        if s == "code_bug":
            return FailureCategory.FUNCTION_BUG
        try:
            return FailureCategory(s)
        except ValueError:
            return FailureCategory.UNKNOWN

    @computed_field
    @property
    def category_label(self) -> str:
        """问题分类的中文显示名。"""
        cat = self.category
        if not isinstance(cat, FailureCategory):
            try:
                cat = FailureCategory(str(cat))
            except ValueError:
                return str(cat)
        return cat.label_zh


class BugRecommendation(BaseModel):
    """A structured bug filing recommendation."""

    summary: str = Field(description="One-line summary suitable as a bug title")
    detail_description: str = Field(
        description="Detailed description covering symptom, impact, evidence, and suggested next steps"
    )
    severity: Optional[Severity] = Field(default=None, description="Suggested bug severity")
    category: Optional[FailureCategory] = Field(default=None, description="Failure category of the bug")
    # Actual stage name (e.g. "#3.2 仿真车部署") — preferred display field.
    affected_stage_name: str = Field(default="", description="Actual Jenkins stage name where the bug manifests")
    # Kept for internal logic only — excluded from JSON output.
    affected_stage: Optional[PipelineStage] = Field(default=None, exclude=True)
    affected_jobs: list[str] = Field(
        default_factory=list,
        description="Downstream Jenkins job names that surfaced the issue (e.g., '部署白泽仿真车 #1717')",
    )

    @computed_field
    @property
    def category_label(self) -> Optional[str]:
        """缺陷分类的中文显示名。"""
        cat = self.category
        if cat is None:
            return None
        if not isinstance(cat, FailureCategory):
            try:
                cat = FailureCategory(str(cat))
            except ValueError:
                return str(cat)
        return cat.label_zh


class AnalysisReport(BaseModel):
    """Complete analysis report for a pipeline failure."""

    job_name: str
    build_number: int
    build_url: Optional[str] = None
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    overall_status: str = Field(description="Overall pipeline status")

    # Actual stage name of the failure point (e.g. "#3.2 仿真车部署/完美仿真车部署").
    failure_stage_name: str = Field(default="", description="Actual Jenkins stage name where failure occurred")
    # Kept for internal logic only — excluded from JSON output.
    failure_stage: PipelineStage = Field(default=PipelineStage.UNKNOWN, exclude=True)

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

    # Module versions from deployment stages (e.g. {"FMS": "v1.2.3", "PP": "v2.0.1"})
    module_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Software module versions detected from deployment logs",
    )

    # Summary
    summary: str = Field(default="", description="AI-generated executive summary")
    bug_recommendations: list[BugRecommendation] = Field(
        default_factory=list,
        description="Structured bug filing recommendations (each with summary + detail_description)",
    )

    # Token usage
    token_usage: Optional[TokenUsage] = Field(
        default=None,
        description="LLM token consumption for this analysis run",
    )

    @field_validator("bug_recommendations", mode="before")
    @classmethod
    def _coerce_recommendations(cls, value):
        if not value:
            return []
        coerced: list = []
        for item in value:
            if isinstance(item, BugRecommendation):
                coerced.append(item)
            elif isinstance(item, dict):
                coerced.append(item)
            elif isinstance(item, str):
                coerced.append(
                    BugRecommendation(summary=item, detail_description=item)
                )
            else:
                coerced.append(item)
        return coerced
