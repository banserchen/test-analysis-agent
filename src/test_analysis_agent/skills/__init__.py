"""Skills package - extensible skill/plugin system for the analysis agent."""

from test_analysis_agent.skills.allure_case_failure_classifier import AllureCaseFailureClassifierSkill
from test_analysis_agent.skills.bug_draft_generator import BugDraftGeneratorSkill
from test_analysis_agent.skills.code_stacktrace_mapper import CodeStacktraceMapperSkill
from test_analysis_agent.skills.env_instability_detector import EnvInstabilityDetectorSkill
from test_analysis_agent.skills.jenkins_downstream_trace import JenkinsDownstreamTraceSkill
from test_analysis_agent.skills.jenkins_log_root_cause import JenkinsLogRootCauseSkill
from test_analysis_agent.skills.report_summary_generator import ReportSummaryGeneratorSkill
from test_analysis_agent.skills.test_bootstrap_failure import TestBootstrapFailureSkill

__all__ = [
    "AllureCaseFailureClassifierSkill",
    "BugDraftGeneratorSkill",
    "CodeStacktraceMapperSkill",
    "EnvInstabilityDetectorSkill",
    "JenkinsDownstreamTraceSkill",
    "JenkinsLogRootCauseSkill",
    "ReportSummaryGeneratorSkill",
    "TestBootstrapFailureSkill",
]
