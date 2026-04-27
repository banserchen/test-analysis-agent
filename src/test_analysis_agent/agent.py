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
    TokenUsage,
    TriggeredJobInfo,
)
from test_analysis_agent.parsers.allure_parser import AllureReportParser
from test_analysis_agent.parsers.jenkins_parser import JenkinsParser, parse_log_text
from test_analysis_agent.skills import (
    AllureCaseFailureClassifierSkill,
    BugDraftGeneratorSkill,
    CodeStacktraceMapperSkill,
    EnvInstabilityDetectorSkill,
    InfrastructureInspectorSkill,
    JenkinsDownstreamTraceSkill,
    JenkinsLogRootCauseSkill,
    PipelineSummaryGeneratorSkill,
    ReportSummaryGeneratorSkill,
    StageFailureAnalyzerSkill,
    TestBootstrapFailureSkill,
    TestFailureBatchAnalyzerSkill,
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
        self._skill_registry.register(TestFailureBatchAnalyzerSkill())
        self._skill_registry.register(StageFailureAnalyzerSkill())
        self._skill_registry.register(PipelineSummaryGeneratorSkill())
        self._skill_registry.register(InfrastructureInspectorSkill())

        # Load custom skills from directory
        if self._settings.skills_dir:
            self._skill_registry.load_from_directory(self._settings.skills_dir)

        # Inject LLM analyzer into all LLM-capable skills so they can use their prompt.md
        self._skill_registry.set_llm_client(self._llm, self._llm._language)

    @property
    def skills(self) -> SkillRegistry:
        """Access the skill registry."""
        return self._skill_registry

    def analyze(self, request: AnalysisRequest) -> AnalysisReport:
        """Run the complete analysis pipeline.

        Analysis path (redesign v3):
        1. Fetch Jenkins build log + wfapi stage data + Jenkinsfile.
        2. Use wfapi stages when available (authoritative); fall back to log parsing.
        3. Find the first genuinely failing stage.
        3a. Test stage: try Allure; fall back to log analysis.
        3b. Non-test stage: trace downstream jobs and extract root cause.
        4. Filter stage_results to only failure/skipped stages (not pass).
        5. Enrich with skills and generate summary.
        """
        logger.info("Starting analysis for %s #%d", request.job_name, request.build_number)
        self._llm.reset_usage()

        # Step 1: Fetch Jenkins build log and ancillary data.
        log_text, build_info = self._fetch_build_data(request)

        # Build URL — prefer Jenkins info; fall back to constructing from URL + job.
        build_url: Optional[str] = build_info.get("url") if build_info else None
        parser: Optional[JenkinsParser] = None
        if self._settings.jenkins_url:
            try:
                parser = JenkinsParser(
                    self._settings.jenkins_url,
                    self._settings.jenkins_username,
                    self._settings.jenkins_password,
                )
                if not build_url:
                    build_url = parser.get_build_url(request.job_name, request.build_number)
            except Exception:
                pass

        # Step 2a: Try to get authoritative stage data from wfapi.
        wfapi_stages: list[dict] = []
        if parser:
            wfapi_stages = parser.get_wfapi_stages(request.job_name, request.build_number)

        # Step 2b: Parse log to identify stages (also used as fallback).
        stage_results = self._build_stage_results(log_text, wfapi_stages, parser, request)

        # Step 2c: Fetch Jenkinsfile for context enrichment.
        jenkinsfile: str = ""
        if parser:
            jenkinsfile = parser.get_jenkinsfile(request.job_name, request.build_number)

        # Step 2d: Extract module versions from build parameters.
        module_versions: dict[str, str] = {}
        if parser:
            try:
                build_params = parser.get_build_parameters(request.job_name, request.build_number)
                module_versions = _extract_module_versions(build_params)
            except Exception as exc:
                logger.warning("Could not extract module versions: %s", exc)

        # Step 3: Find the first genuinely failing stage.
        first_failing = self._find_first_failing_stage(stage_results)

        # Fallback for ABORTED builds: if no stage has status "fail", look for the
        # most recently active stage (status "skipped" due to abort) so we can still
        # parse its allure report and set a meaningful failure_stage_name.
        aborted_test_stage: Optional[PipelineStageResult] = None
        if first_failing is None:
            for s in stage_results:
                if s.status == "skipped" and self._is_test_stage(s.stage_name):
                    aborted_test_stage = s
                    break

        if first_failing:
            fs = first_failing.failing_sub_stage or ""
            failure_stage_name = (
                fs if fs and re.match(r"#\d+\.\d+", fs)
                else first_failing.stage_name or "其他"
            )
        elif aborted_test_stage:
            failure_stage_name = aborted_test_stage.stage_name
        else:
            failure_stage_name = "其他"
        failure_stage = (
            first_failing.stage if first_failing
            else (aborted_test_stage.stage if aborted_test_stage else PipelineStage.UNKNOWN)
        )

        # Step 4: Analyse based on failure type.
        issues: list[AnalyzedIssue] = []
        primary_stage: Optional[PipelineStageResult] = None
        test_failures: list[TestCaseFailure] = []
        test_stats: dict = {"total": 0, "passed": 0, "failed": 0, "broken": 0, "skipped": 0}

        active_stage = first_failing or aborted_test_stage  # the stage to analyse

        if active_stage is None:
            pass
        elif self._is_test_stage(active_stage.stage_name):
            allure_data = self._parse_allure_report(request, build_url=build_url)
            if allure_data and allure_data.has_test_results():
                test_stats = allure_data.statistics
                test_failures = allure_data.get_failures()
                test_failures = self._analyze_test_failures(test_failures, request)
                # issues are produced by TestFailureBatchAnalyzerSkill in step 6
                primary_stage = active_stage
            else:
                self._analyze_triggered_jobs(request, stage_results)
                issues, primary_stage = self._analyze_stage_failures(stage_results)
        else:
            self._analyze_triggered_jobs(request, stage_results)
            issues, primary_stage = self._analyze_stage_failures(stage_results)

        # Step 5: Filter stage_results to only failure/skipped stages for output.
        failure_stage_results = [s for s in stage_results if s.status not in ("pass", "success")]

        # Step 6: Run skill-based analysis and enrich.
        # primary_stage is passed so StageFailureAnalyzerSkill can do LLM enrichment.
        skill_context = {
            "log_text": log_text,
            "stage_results": stage_results,
            "test_failures": test_failures,
            "request": request,
            "failure_stage_name": failure_stage_name,
            "jenkinsfile": jenkinsfile,
            "primary_stage": primary_stage,
            "build_info": build_info or {},
            "environments_api_url": self._settings.environments_api_url,
        }
        skill_results = self._skill_registry.execute_applicable(skill_context)
        issues = self._enrich_with_skill_findings(issues, skill_results)

        # Step 7: Generate summary via PipelineSummaryGeneratorSkill.
        summary_context = {
            "job_name": request.job_name,
            "build_number": request.build_number,
            "failure_stage": failure_stage,
            "failure_stage_name": failure_stage_name,
            "issues": issues,
            "total_tests": test_stats.get("total", 0),
            "passed_tests": test_stats.get("passed", 0),
            "failed_tests": test_stats.get("failed", 0),
            "broken_tests": test_stats.get("broken", 0),
        }
        summary_result = self._skill_registry.execute_skill("pipeline_summary_generator", summary_context)
        summary = summary_result.get("summary", "Analysis completed.")
        bug_recs = summary_result.get("bug_recommendations", [])

        # Step 8: Build the report (only failing stage_results in output).
        llm_usage, llm_calls = self._llm.get_total_usage()
        report = AnalysisReport(
            job_name=request.job_name,
            build_number=request.build_number,
            build_url=build_url,
            overall_status=build_info.get("result", "FAILURE") if build_info else "FAILURE",
            failure_stage_name=failure_stage_name,
            failure_stage=failure_stage,
            stage_results=failure_stage_results,
            total_tests=test_stats.get("total", 0),
            passed_tests=test_stats.get("passed", 0),
            failed_tests=test_stats.get("failed", 0),
            broken_tests=test_stats.get("broken", 0),
            skipped_tests=test_stats.get("skipped", 0),
            test_failures=test_failures,
            issues=issues,
            module_versions=module_versions,
            summary=summary,
            bug_recommendations=bug_recs,
            token_usage=TokenUsage(
                llm_model=self._llm._model,
                prompt_tokens=llm_usage.prompt_tokens,
                completion_tokens=llm_usage.completion_tokens,
                total_tokens=llm_usage.total_tokens,
                llm_calls=llm_calls,
            ),
        )

        # Step 9: Apply skill post-processing.
        report = self._skill_registry.apply_post_processing(report)

        logger.info(
            "Analysis complete for %s #%d: %d issues found",
            request.job_name, request.build_number, len(issues),
        )
        return report

    def analyze_log_text(self, log_text: str, job_name: str = "unknown", build_number: int = 0) -> AnalysisReport:
        """Analyze a raw log text directly without Jenkins connection."""
        stage_results = parse_log_text(log_text)
        first_failing = self._find_first_failing_stage(stage_results)
        failure_stage_name = first_failing.stage_name if first_failing else "其他"
        failure_stage = first_failing.stage if first_failing else PipelineStage.UNKNOWN

        issues, primary_stage = self._analyze_stage_failures(stage_results)

        skill_context = {"log_text": log_text, "stage_results": stage_results, "primary_stage": primary_stage}
        skill_results = self._skill_registry.execute_applicable(skill_context)
        issues = self._enrich_with_skill_findings(issues, skill_results)

        summary_result = self._skill_registry.execute_skill("pipeline_summary_generator", {
            "job_name": job_name,
            "build_number": build_number,
            "failure_stage": failure_stage,
            "failure_stage_name": failure_stage_name,
            "issues": issues,
            "total_tests": 0, "passed_tests": 0, "failed_tests": 0, "broken_tests": 0,
        })
        summary = summary_result.get("summary", "Analysis completed.")
        bug_recs = summary_result.get("bug_recommendations", [])

        report = AnalysisReport(
            job_name=job_name,
            build_number=build_number,
            overall_status="FAILURE",
            failure_stage_name=failure_stage_name,
            failure_stage=failure_stage,
            stage_results=[s for s in stage_results if s.status not in ("pass", "success")],
            issues=issues,
            summary=summary,
            bug_recommendations=bug_recs,
        )

        return self._skill_registry.apply_post_processing(report)

    def _build_stage_results(
        self,
        log_text: str,
        wfapi_stages: list[dict],
        parser: Optional["JenkinsParser"],
        request: AnalysisRequest,
    ) -> list[PipelineStageResult]:
        """Build stage results, preferring wfapi data over log parsing.

        When wfapi_stages is available we use it as the authoritative stage list
        (accurate names, statuses, and error messages). Log parsing is still used
        to extract triggered job information.

        The wfapi status values: SUCCESS, FAILED, IN_PROGRESS, PAUSED_PENDING_INPUT,
        NOT_EXECUTED → mapped to: pass, fail, unknown, unknown, skipped.
        """
        if not wfapi_stages:
            return parse_log_text(log_text)

        # Map wfapi status to our internal status values.
        _STATUS_MAP = {
            "SUCCESS": "pass",
            "FAILED": "fail",
            "NOT_EXECUTED": "skipped",
            "IN_PROGRESS": "unknown",
            "PAUSED_PENDING_INPUT": "unknown",
            "ABORTED": "skipped",
        }

        # First pass log parse to get triggered job info (wfapi doesn't cover this).
        log_parsed = parse_log_text(log_text)
        # Build a lookup: stage_name → triggered jobs from log parsing.
        log_stage_by_name: dict[str, PipelineStageResult] = {s.stage_name: s for s in log_parsed}

        results: list[PipelineStageResult] = []
        for wf in wfapi_stages:
            name: str = wf.get("name", "")
            raw_status: str = wf.get("status", "").upper()
            status = _STATUS_MAP.get(raw_status, "unknown")

            # Error message directly from wfapi (much cleaner than log parsing).
            error_info = wf.get("error") or {}
            error_summary: Optional[str] = error_info.get("message") if error_info else None

            # Try log-parsed match first (exact name, then major-number prefix).
            log_match = log_stage_by_name.get(name)
            if log_match is None:
                # Try matching by major stage number prefix (e.g. "#2" matches "#2.1 ...")
                m = re.match(r"(#\d+)\s", name)
                if m:
                    prefix = m.group(1) + " "
                    for k, v in log_stage_by_name.items():
                        if k.startswith(prefix) or k == m.group(1):
                            log_match = v
                            break

            sub_stages = log_match.sub_stages if log_match else []
            failing_sub_stage = log_match.failing_sub_stage if log_match else None
            triggered_jobs = log_match.triggered_jobs if log_match else []
            log_excerpt = log_match.log_excerpt if log_match else None

            # For failed stages, fetch the stage-specific log via wfapi node log.
            if status == "fail" and parser and not log_excerpt:
                node_id = str(wf.get("id", ""))
                if node_id:
                    stage_log = parser.get_stage_log(node_id, request.job_name, request.build_number)
                    if stage_log:
                        # Keep last 3000 chars as excerpt.
                        log_excerpt = stage_log[-3000:] if len(stage_log) > 3000 else stage_log

            # For failed stages without error info, fall back to log-parsed error.
            if status == "fail" and not error_summary and log_match and log_match.error_summary:
                error_summary = log_match.error_summary

            # Classify stage type (legacy field, used internally only).
            from test_analysis_agent.parsers.jenkins_parser import _classify_stage_type
            stage_type = _classify_stage_type(name)

            results.append(PipelineStageResult(
                stage_name=name,
                stage=stage_type,
                status=status,
                error_summary=error_summary,
                log_excerpt=log_excerpt,
                triggered_jobs=triggered_jobs,
                sub_stages=sub_stages,
                failing_sub_stage=failing_sub_stage,
            ))

        return results

    def _find_first_failing_stage(
        self, stage_results: list[PipelineStageResult]
    ) -> Optional[PipelineStageResult]:
        """Return the first stage that actually failed (not just skipped).

        Preference order:
        1. First stage with ``status == "fail"`` that owns a failing downstream job.
        2. First stage with ``status == "fail"``.
        """
        for result in stage_results:
            if result.status == "fail" and any(
                j.status in ("failure", "unstable") for j in result.triggered_jobs
            ):
                return result
        for result in stage_results:
            if result.status == "fail":
                return result
        return None

    @staticmethod
    def _is_test_stage(stage_name: str) -> bool:
        """Return True if a stage name implies it is a testing stage."""
        return bool(re.search(r"(?:测试|test|qa|verify|allure|robot|上报测试结果|执行测试)", stage_name, re.IGNORECASE))

    @staticmethod
    def _auto_allure_url(build_url: str) -> str:
        """Construct the standard Allure report URL from a Jenkins build URL."""
        return build_url.rstrip("/") + "/allure"

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
        """Determine which stage caused the failure (legacy helper for backward compat)."""
        first_failing = self._find_first_failing_stage(stage_results)
        if first_failing:
            return first_failing.stage
        for result in stage_results:
            if result.status not in ("pass", "success"):
                return result.stage
        return PipelineStage.UNKNOWN

    def _analyze_triggered_jobs(self, request: AnalysisRequest, stage_results: list[PipelineStageResult]) -> None:
        """Analyze triggered job failures by recursively fetching their logs.

        For every failing downstream job we:
          1. Fetch its log.
          2. Run ``parse_log_text`` to identify its own stages/errors.
          3. Attach a compact root-cause excerpt back to the parent stage.
          4. Recurse into the downstream job's own failing triggered jobs so
             we reach the actual leaf failure.
        """
        jenkins_url = request.jenkins_url or self._settings.jenkins_url
        if not jenkins_url:
            return

        try:
            parser = JenkinsParser(
                jenkins_url, self._settings.jenkins_username, self._settings.jenkins_password
            )
        except Exception as exc:
            logger.warning("Could not initialise Jenkins parser: %s", exc)
            return

        visited: set[tuple[str, int]] = set()
        for stage in stage_results:
            for job in stage.triggered_jobs:
                if job.status in ("failure", "unstable"):
                    self._trace_failing_job(parser, stage, job, visited, depth=0)

    def _trace_failing_job(
        self,
        parser: "JenkinsParser",
        parent_stage: PipelineStageResult,
        job: "object",
        visited: set[tuple[str, int]],
        depth: int,
    ) -> None:
        """Recursively fetch and summarise a failing downstream job.

        In addition to bullet-line error roll-up, we now analyse the
        downstream log for Ansible TASK boundaries to localise the failing
        task by name (e.g. ``TASK [标记健康检查失败的仿真车容器]``) and capture
        a short context excerpt. This gives the agent a concrete semantic
        anchor without needing an LLM round-trip.
        """
        key = (job.job_name, job.build_number)
        if key in visited or depth > 4:
            return
        visited.add(key)

        try:
            triggered_log = parser.get_triggered_job_log(job.job_name, job.build_number)
        except Exception as exc:
            logger.warning("Failed to fetch downstream log for %s #%d: %s", job.job_name, job.build_number, exc)
            return
        if not triggered_log:
            return

        # --- Ansible-aware TASK extraction -----------------------------
        ansible_info = _extract_failing_ansible_task(triggered_log)
        if ansible_info:
            job.failing_task = ansible_info.get("task_name")
            job.root_cause_hint = ansible_info.get("hint")
            job.error_excerpt = ansible_info.get("excerpt")

        indent = "  " * depth
        downstream_results = parse_log_text(triggered_log)
        bullet_lines: list[str] = []
        nested_failures: list[object] = []
        for result in downstream_results:
            if result.error_summary:
                for line in result.error_summary.splitlines()[:8]:
                    bullet_lines.append(f"{indent}[{job.job_name} #{job.build_number}] {line}")
            for sub_job in result.triggered_jobs:
                if sub_job.status in ("failure", "unstable"):
                    nested_failures.append(sub_job)

        # Lead the bullet list with the ansible TASK anchor so the root cause
        # is the first thing a human sees.
        if ansible_info and ansible_info.get("task_name"):
            anchor = (
                f"{indent}[{job.job_name} #{job.build_number}] "
                f"↪ 失败 TASK: {ansible_info['task_name']}"
            )
            bullet_lines.insert(0, anchor)
            for line in (ansible_info.get("excerpt") or "").splitlines()[:6]:
                bullet_lines.insert(
                    1, f"{indent}[{job.job_name} #{job.build_number}]     {line}"
                )

        if bullet_lines:
            existing = parent_stage.error_summary or ""
            header = "\nTriggered job errors:\n" if depth == 0 else f"\n↳ Downstream (depth {depth}) errors:\n"
            parent_stage.error_summary = f"{existing}{header}" + "\n".join(bullet_lines)

        # Try to extract a short URL hint for the downstream build.
        try:
            job.url = parser.get_build_url(job.job_name, job.build_number)
        except Exception:
            pass

        for sub_job in nested_failures:
            self._trace_failing_job(parser, parent_stage, sub_job, visited, depth + 1)

    def _parse_allure_report(
        self,
        request: AnalysisRequest,
        build_url: Optional[str] = None,
    ) -> Optional[object]:
        """Parse Allure report if available.

        URL resolution order:
        1. ``request.allure_report_url`` (explicit override)
        2. Auto-detected ``<build_url>/allure`` (from Jenkins build info)
        3. ``request.allure_report_path`` (local file)
        """
        allure_url = request.allure_report_url
        if not allure_url and build_url:
            allure_url = self._auto_allure_url(build_url)

        if allure_url or request.allure_report_path:
            try:
                parser = AllureReportParser(
                    report_path=request.allure_report_path,
                    report_url=allure_url,
                )
                return parser.parse()
            except Exception as exc:
                logger.warning("Allure report unavailable (%s): %s", allure_url, exc)
        return None

    def _analyze_stage_failures(
        self, stage_results: list[PipelineStageResult]
    ) -> tuple[list[AnalyzedIssue], Optional[PipelineStageResult]]:
        """Analyze pipeline stage failures and produce deterministic issues.

        For each failing downstream job we produce one issue with:
        - Title:       ``下游任务失败：<job> #<num>（失败 TASK: <task>）``
        - Description: actual Jenkins stage name + ansible root cause
        - Category:    derived from the stage's best-effort PipelineStage enum

        Cascade side-effect stages (those that failed only because of an upstream
        failure) are summarised as LOW-severity notes, de-duped by stage_name.
        Skipped stages are ignored entirely.

        Returns (issues, primary_stage). primary_stage is passed into the skill
        context so StageFailureAnalyzerSkill can do LLM enrichment.
        """
        issues: list[AnalyzedIssue] = []
        first_failing = next((s for s in stage_results if s.status == "fail"), None)
        if first_failing is None:
            return issues, None

        # Gather all (stage, job) pairs for failing downstream jobs.
        failing_pairs: list[tuple[PipelineStageResult, TriggeredJobInfo]] = []
        for stage in stage_results:
            for job in stage.triggered_jobs:
                if job.status in ("failure", "unstable"):
                    failing_pairs.append((stage, job))

        primary_stage = failing_pairs[0][0] if failing_pairs else first_failing
        # Human-readable label: use stage_name, not failing_sub_stage (which may be
        # an internal "Branch:" or "Declarative:" marker).
        primary_label = primary_stage.stage_name or primary_stage.stage.value

        # (1) One issue per failing downstream job.
        for stage, job in failing_pairs:
            stage_label = stage.stage_name or stage.stage.value
            root_cause = (
                getattr(job, "root_cause_hint", None)
                or getattr(job, "failing_task", None)
                or "下游任务构建失败，请查看该 job 日志定位具体 TASK"
            )
            title = f"下游任务失败：{job.job_name} #{job.build_number}"
            if getattr(job, "failing_task", None):
                title += f"（失败 TASK: {job.failing_task}）"
            description = (
                f"流水线阶段「{stage_label}」调用下游 job `{job.job_name} #{job.build_number}` "
                f"失败，这是本次构建的直接根因。"
            )
            if getattr(job, "error_excerpt", None):
                description += f"\n\n失败 TASK 上下文：\n{job.error_excerpt}"
            issues.append(
                AnalyzedIssue(
                    title=title,
                    description=description,
                    stage_name=stage_label,
                    category=FailureCategory.DEPLOYMENT_ERROR
                    if stage.stage == PipelineStage.DEPLOYMENT
                    else FailureCategory.UNKNOWN,
                    severity=Severity.HIGH,
                    stage=stage.stage,
                    root_cause=root_cause,
                    suggestion=(
                        f"进入 {job.url or f'{job.job_name} #{job.build_number}'} 查看完整日志，"
                        f"结合 Jenkins workspace 下对应的 Ansible playbook / 脚本定位 TASK "
                        f"`{getattr(job, 'failing_task', '')}` 的失败原因。"
                    ),
                    should_file_bug=True,
                    bug_summary=title,
                    log_evidence=getattr(job, "error_excerpt", None),
                )
            )

        # (2) LLM enrichment for primary stage is now handled by StageFailureAnalyzerSkill.
        #     The skill receives primary_stage via the skill context.

        # (3) Cascade notes ONLY for failing stages that come AFTER the primary
        # in the pipeline.  Stages that appear BEFORE the primary either have
        # independent issues or contain false-positive error noise — do not
        # mis-label them as cascade effects of the primary failure.
        primary_idx = next(
            (i for i, s in enumerate(stage_results) if s is primary_stage), len(stage_results)
        )
        stages_with_failing_jobs = {id(s) for s, _ in failing_pairs}
        emitted_cascade: set[str] = set()
        for stage in stage_results[primary_idx + 1:]:
            if stage.status != "fail":
                continue
            if id(stage) in stages_with_failing_jobs:
                continue
            key = stage.stage_name or stage.stage.value
            if key in emitted_cascade:
                continue
            emitted_cascade.add(key)
            issues.append(
                AnalyzedIssue(
                    title=f"阶段「{key}」受上游失败影响未成功",
                    description=(
                        "该阶段的报错是上游阶段失败后的级联效应，不应视为独立根因。"
                        "请优先排查首个失败阶段。"
                    ),
                    stage_name=key,
                    category=FailureCategory.UNKNOWN,
                    severity=Severity.LOW,
                    stage=stage.stage,
                    root_cause=f"上游「{primary_label}」阶段失败导致级联",
                    suggestion="先修复首个失败阶段后再观察该阶段。",
                    should_file_bug=False,
                )
            )
        return issues, primary_stage

    def _analyze_test_failures(
        self,
        failures: list[TestCaseFailure],
        request: AnalysisRequest,
    ) -> list[TestCaseFailure]:
        """Populate related_code for each failure (no LLM calls — handled by skills).

        LLM analysis (ai_analysis, failure_category) is performed by:
        - AllureCaseFailureClassifierSkill  → per-failure ai_analysis + failure_category
        - TestFailureBatchAnalyzerSkill     → batch grouping into AnalyzedIssue list
        Both skills receive test_failures via skill_context in step 6.
        """
        for failure in failures:
            failure.related_code = self._find_test_code(failure, request)
        return failures


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
        """Enrich analysis issues with findings from skills.

        Handles two skill output types:
        - ``analyzed_issues``: structured AnalyzedIssue dicts from TestFailureBatchAnalyzerSkill
        - ``pattern_findings``: pattern-match findings from heuristic skills
        """
        for result in skill_results:
            if result.get("_error"):
                continue

            # StageFailureAnalyzerSkill: LLM-generated issue for the primary stage
            llm_stage_issue = result.get("llm_stage_issue")
            if llm_stage_issue and isinstance(llm_stage_issue, dict):
                title = llm_stage_issue.get("title", "")
                if title and not any(i.title == title for i in issues):
                    issues.append(
                        AnalyzedIssue(
                            title=title,
                            description=llm_stage_issue.get("description", ""),
                            category=_safe_failure_category(llm_stage_issue.get("category", "unknown")),
                            severity=_safe_severity_enum(llm_stage_issue.get("severity", "medium")),
                            stage=PipelineStage.UNKNOWN,
                            root_cause=llm_stage_issue.get("root_cause"),
                            suggestion=llm_stage_issue.get("suggestion"),
                            should_file_bug=bool(llm_stage_issue.get("should_file_bug", False)),
                            bug_summary=llm_stage_issue.get("bug_summary"),
                        )
                    )

            # TestFailureBatchAnalyzerSkill: structured issues that replace/augment existing ones
            for item in result.get("analyzed_issues", []):
                issues.append(
                    AnalyzedIssue(
                        title=item.get("title", "测试失败组"),
                        description=item.get("description", ""),
                        category=_safe_failure_category(item.get("category", "test_case_failure")),
                        severity=_safe_severity_enum(item.get("severity", "medium")),
                        stage=PipelineStage.TESTING,
                        affected_tests=item.get("affected_tests", []),
                        root_cause=item.get("root_cause"),
                        suggestion=item.get("suggestion"),
                        should_file_bug=bool(item.get("should_file_bug", False)),
                        bug_summary=item.get("bug_summary"),
                    )
                )

            # Heuristic pattern-finding skills
            # Build a combined set of text from all LLM-analyzed issues to detect overlap.
            llm_issue_text = " ".join(
                f"{i.title} {i.description or ''} {i.root_cause or ''}"
                for i in issues
            ).lower()
            # Keywords that indicate connectivity is already analyzed by LLM
            _CONNECTIVITY_KEYWORDS = {"connection", "connect", "refused", "timeout", "unreachable", "不可达", "超时", "连接"}

            for finding in result.get("pattern_findings", []):
                category = finding.get("category", "unknown")

                # Skip if an issue with identical category already exists
                if any(i.category.value == category for i in issues):
                    continue

                # Skip network/timeout pattern findings if LLM has already produced a
                # more detailed connectivity issue (avoids duplicate noise entries).
                if category in ("network_error", "timeout_error"):
                    if any(kw in llm_issue_text for kw in _CONNECTIVITY_KEYWORDS):
                        continue

                match_count = int(finding.get("match_count", "1"))
                # Pure pattern findings are low-severity supplementary info unless
                # there are many matches (≥10 = medium, ≥1 = low).
                severity = Severity.MEDIUM if match_count >= 10 else Severity.LOW
                log_evidence = finding.get("log_evidence") or finding.get("sample_match")
                issues.append(
                    AnalyzedIssue(
                        title=finding.get("description", "Pattern match"),
                        description=(
                            f"日志中检测到 {match_count} 处匹配（{finding.get('description', '')}）。"
                            f"以下为部分日志证据，请结合上下文判断是否为本次失败的根因。"
                        ),
                        category=_safe_failure_category(category),
                        severity=severity,
                        stage=PipelineStage.UNKNOWN,
                        should_file_bug=False,
                        log_evidence=log_evidence,
                    )
                )
        return issues


def _safe_failure_category(value: str) -> FailureCategory:
    """Safely convert string to FailureCategory, falling back to UNKNOWN."""
    try:
        return FailureCategory(value)
    except ValueError:
        return FailureCategory.UNKNOWN


def _safe_severity_enum(value: str) -> Severity:
    """Safely convert string to Severity, falling back to MEDIUM."""
    try:
        return Severity(value)
    except ValueError:
        return Severity.MEDIUM


# Parameter names that contain module version info, mapped to a friendly display name.
_VERSION_PARAM_MAP: dict[str, str] = {
    "FMS_VERSION": "FMS",
    "PP_VERSION": "PP",
    "PP2_VERSION": "PP2",
    "welldrive_group_version": "WellDrive",
    "qpilot_group_version": "QPilot",
    "MAP_PACKAGE_VERSION": "地图包",
    "MAP_PACKAGE_VERSION2": "地图包2",
}


def _extract_module_versions(params: dict[str, str]) -> dict[str, str]:
    """Extract a friendly module_name → version dict from build parameters.

    Non-empty version params are included; empty ones are skipped.
    Map package versions are combined with their package name for clarity.
    """
    versions: dict[str, str] = {}
    for param, label in _VERSION_PARAM_MAP.items():
        val = params.get(param, "").strip()
        if not val:
            continue
        # For map packages, prepend the package name if available.
        if param == "MAP_PACKAGE_VERSION":
            pkg = params.get("MAP_PACKAGE_NAME", "").strip()
            val = f"{pkg} {val}" if pkg else val
        elif param == "MAP_PACKAGE_VERSION2":
            pkg = params.get("MAP_PACKAGE_NAME2", "").strip()
            val = f"{pkg} {val}" if pkg else val
        versions[label] = val
    return versions


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


# ---------------------------------------------------------------------------
# Ansible / shell TASK extraction for downstream job logs
# ---------------------------------------------------------------------------
_ANSIBLE_TASK_HEADER_RE = re.compile(r"^TASK\s*\[(?P<name>[^\]]+)\]\s*\*+\s*$")
_ANSIBLE_FATAL_RE = re.compile(r"^fatal:\s*\[[^\]]+\]:\s*FAILED!", re.IGNORECASE)
_ANSIBLE_FAILED_BLOCK_RE = re.compile(r"\bfailed=([1-9]\d*)\b")
_ANSIBLE_MSG_RE = re.compile(r'"msg"\s*:\s*"([^"]+)"')


def _extract_failing_ansible_task(log_text: str) -> Optional[dict]:
    """Locate the failing Ansible TASK in a downstream job log.

    Returns a dict ``{task_name, hint, excerpt}`` or ``None`` if no TASK
    structure is detected.

    Tasks whose fatal line is followed by ``...ignoring`` (``ignore_errors: yes``)
    are skipped: they aren't the real cause of the job failing. Among the
    remaining genuinely-failing tasks, we return the **last** one — typically
    the terminating / fail-fast task that actually caused the job to abort.
    """
    lines = log_text.splitlines()
    current_task: Optional[str] = None
    current_task_lines: list[str] = []
    failing_tasks: list[dict] = []

    def _finalize_current() -> None:
        if current_task is None:
            return
        has_fatal = any(_ANSIBLE_FATAL_RE.search(ln) for ln in current_task_lines)
        if not has_fatal:
            return
        # ignore_errors path: ansible prints "...ignoring" after the fatal.
        is_ignored = any(
            re.search(r"\.\.\.ignoring\b", ln, re.IGNORECASE)
            for ln in current_task_lines
        )
        if is_ignored:
            return
        msgs: list[str] = []
        for ln in current_task_lines:
            m = _ANSIBLE_MSG_RE.search(ln)
            if m:
                msgs.append(m.group(1))
        failing_tasks.append(
            {
                "task_name": current_task,
                "msgs": msgs,
                "excerpt_lines": current_task_lines[-12:],
            }
        )

    for line in lines:
        m = _ANSIBLE_TASK_HEADER_RE.match(line.strip())
        if m:
            _finalize_current()
            current_task = m.group("name").strip()
            current_task_lines = [line]
            continue
        if current_task is not None:
            current_task_lines.append(line)
    _finalize_current()

    if not failing_tasks:
        return None

    chosen = failing_tasks[-1]
    hint_parts = [f"Ansible TASK [{chosen['task_name']}] 失败"]
    if chosen["msgs"]:
        hint_parts.append("msg: " + "; ".join(chosen["msgs"][:2]))
    return {
        "task_name": chosen["task_name"],
        "hint": " — ".join(hint_parts),
        "excerpt": "\n".join(chosen["excerpt_lines"]),
    }
