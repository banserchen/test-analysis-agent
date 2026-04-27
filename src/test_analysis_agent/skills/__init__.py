"""技能包——分析 Agent 的可扩展技能/插件系统。"""

from test_analysis_agent.skills.allure_case_failure_classifier import AllureCaseFailureClassifierSkill
from test_analysis_agent.skills.bug_draft_generator import BugDraftGeneratorSkill
from test_analysis_agent.skills.code_stacktrace_mapper import CodeStacktraceMapperSkill
from test_analysis_agent.skills.env_instability_detector import EnvInstabilityDetectorSkill
from test_analysis_agent.skills.infrastructure_inspector import InfrastructureInspectorSkill
from test_analysis_agent.skills.jenkins_downstream_trace import JenkinsDownstreamTraceSkill
from test_analysis_agent.skills.jenkins_log_root_cause import JenkinsLogRootCauseSkill
from test_analysis_agent.skills.pipeline_summary_generator import PipelineSummaryGeneratorSkill
from test_analysis_agent.skills.report_summary_generator import ReportSummaryGeneratorSkill
from test_analysis_agent.skills.stage_failure_analyzer import StageFailureAnalyzerSkill
from test_analysis_agent.skills.test_bootstrap_failure import TestBootstrapFailureSkill
from test_analysis_agent.skills.test_failure_batch_analyzer import TestFailureBatchAnalyzerSkill

__all__ = [
    "AllureCaseFailureClassifierSkill",
    "BugDraftGeneratorSkill",
    "CodeStacktraceMapperSkill",
    "EnvInstabilityDetectorSkill",
    "InfrastructureInspectorSkill",
    "JenkinsDownstreamTraceSkill",
    "JenkinsLogRootCauseSkill",
    "PipelineSummaryGeneratorSkill",
    "ReportSummaryGeneratorSkill",
    "StageFailureAnalyzerSkill",
    "TestBootstrapFailureSkill",
    "TestFailureBatchAnalyzerSkill",
]
