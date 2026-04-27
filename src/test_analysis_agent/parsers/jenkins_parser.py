"""Jenkins log fetcher and parser.

Fetches build logs from Jenkins and identifies pipeline stages, errors,
and triggered downstream jobs.
"""

from __future__ import annotations

import html as _html_lib
import logging
import re
from typing import Optional

import jenkins
import requests

from test_analysis_agent.models.schemas import (
    PipelineStage,
    PipelineStageResult,
    TriggeredJobInfo,
)

logger = logging.getLogger(__name__)

# Real failure signals. We deliberately exclude noisy matches that used to
# produce false positives such as `git ... timeout=10` (argument to git) or
# ansible `"failed": false` JSON fragments.
_ERROR_PATTERNS = [
    re.compile(r"\b(?:ERROR|FATAL)\b[:\s]", re.IGNORECASE),
    re.compile(r"\bException\b(?:[:\s]|$)"),
    re.compile(r"\b(?:FAILURE|FAILED)\b(?![\"'])"),
    re.compile(r"\bBuild\s+failed\b", re.IGNORECASE),
    re.compile(r"exit\s+code\s*[:=]?\s*[1-9]", re.IGNORECASE),
    re.compile(r"\bnon-zero\s+exit\b", re.IGNORECASE),
    re.compile(r"\bcommand\s+not\s+found\b", re.IGNORECASE),
    re.compile(r"\b(?:Connection\s+refused|timed\s+out|unreachable)\b", re.IGNORECASE),
    re.compile(r"\btimeout(?:\s+(?:of|after|expired|exceeded|reached))\b", re.IGNORECASE),
    re.compile(r"\b(?:Permission\s+denied|Access\s+denied|Unauthorized)\b", re.IGNORECASE),
    re.compile(r"\b(?:No\s+space\s+left|Out\s+of\s+memory|OOM|Cannot\s+allocate)\b", re.IGNORECASE),
    re.compile(r"completed\s+(?:with\s+(?:status|result)\s+)?FAILURE", re.IGNORECASE),
    re.compile(r"completed:\s*FAILURE", re.IGNORECASE),
    re.compile(r"Finished:\s*FAILURE", re.IGNORECASE),
]

# Substrings that look like errors but are actually benign noise.
_ERROR_NOISE_PATTERNS = [
    re.compile(r'"failed"\s*:\s*false', re.IGNORECASE),
    re.compile(r"failed=0\b"),
    re.compile(r"\btimeout=\d+\b"),  # e.g. git's `# timeout=10` command argument
    re.compile(r"skipped\s+due\s+to\s+earlier\s+failure", re.IGNORECASE),
    # Docker cleanup: "No such container" is expected when removing a container that
    # may not exist yet; it does NOT indicate a real failure.
    re.compile(r"No\s+such\s+container\b", re.IGNORECASE),
    # Docker official-image guidance message that mentions ERROR but is informational.
    re.compile(r"the\s+container\s+started\s+but\s+didn.t\s+run\s+the\s+expected\s+command", re.IGNORECASE),
    re.compile(r"ENTRYPOINT.*consistency", re.IGNORECASE),
]

_SKIPPED_STAGE_PATTERN = re.compile(r"Stage\s+\"[^\"]+\"\s+skipped\s+due\s+to\s+earlier\s+failure", re.IGNORECASE)

# Triggered downstream job detection. We match only explicit trigger markers.
_TRIGGERED_JOB_START_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Starting\s+building:\s+(?P<job>\S.*?)\s+#(?P<num>\d+)\b", re.IGNORECASE),
    re.compile(r"Triggering\s+(?:a\s+new\s+)?build\s+of\s+(?:job\s+)?['\"]?(?P<job>[^\s'\"#]+)['\"]?\s+#(?P<num>\d+)",
               re.IGNORECASE),
    re.compile(r"Triggering\s+(?:job\s+)?['\"]?(?P<job>[^\s'\"#]+)['\"]?\s+#(?P<num>\d+)", re.IGNORECASE),
]

_TRIGGERED_JOB_RESULT_PATTERN = re.compile(
    r"(?:Build\s+|job\s+)?['\"]?(?P<job>[^\s#'\"]+)['\"]?\s+#(?P<num>\d+)\s+completed"
    r"(?:\s+with\s+(?:status|result))?[:\s]+(?P<status>SUCCESS|FAILURE|UNSTABLE|ABORTED)",
    re.IGNORECASE,
)

_RESERVED_JOB_TOKENS = {"building", "building:", "build", "job", "project", "project:", ""}

# ---------------------------------------------------------------------------
# Best-effort PipelineStage classifier (used only to populate the legacy
# ``stage`` field for skill/report backward compatibility). Stage grouping
# and naming now uses the real Jenkins stage names from the log.
# ---------------------------------------------------------------------------
_STAGE_TYPE_PATTERNS: dict[PipelineStage, list[re.Pattern[str]]] = {
    PipelineStage.PREPARATION: [
        re.compile(r"(?:准备|Preparation|Setup|Environment|Init|Checkout|读取配置|确定地图版本|环境清理|清除上次|Cleanup)", re.IGNORECASE),
        re.compile(r"Declarative:\s*Checkout\s*SCM", re.IGNORECASE),
    ],
    PipelineStage.DEPLOYMENT: [
        re.compile(r"(?:部署|Deploy|Rollout|Release|更新\s*FMS|更新\s*PP|更新.*版本|仿真车部署|完美仿真车部署|切换测试环境|添加FMS|取消当前.*任务|mapping)", re.IGNORECASE),
    ],
    PipelineStage.TESTING: [
        re.compile(r"(?:测试|Test|QA|Verify|Validation|执行测试|上报测试结果|测试结果回传|Declarative:\s*Post\s*Actions)", re.IGNORECASE),
    ],
}


def _classify_stage_type(name: str) -> PipelineStage:
    """Best-effort classification of a stage name into the legacy PipelineStage enum."""
    for stage, patterns in _STAGE_TYPE_PATTERNS.items():
        for pat in patterns:
            if pat.search(name):
                return stage
    return PipelineStage.UNKNOWN


class JenkinsParser:
    """Fetches and parses Jenkins build logs."""

    def __init__(self, url: str, username: str, password: str):
        self._server = jenkins.Jenkins(url, username=username, password=password)
        # Disable proxy for the python-jenkins internal requests session so that
        # Jenkins (an internal service) is reachable directly without a forward proxy.
        if hasattr(self._server, "_session"):
            self._server._session.proxies.update({"http": "", "https": ""})
        self._url = url.rstrip("/")

    def get_build_log(self, job_name: str, build_number: int) -> str:
        """Fetch the console output for a build."""
        try:
            return self._server.get_build_console_output(job_name, build_number)
        except jenkins.JenkinsException as exc:
            logger.error("Failed to fetch log for %s #%d: %s", job_name, build_number, exc)
            raise

    def get_build_info(self, job_name: str, build_number: int) -> dict:
        """Fetch build metadata."""
        try:
            return self._server.get_build_info(job_name, build_number)
        except jenkins.JenkinsException as exc:
            logger.error("Failed to fetch build info for %s #%d: %s", job_name, build_number, exc)
            raise

    def get_build_parameters(self, job_name: str, build_number: int) -> dict[str, str]:
        """Fetch build parameters as a flat name→value dict.

        Reads from the ParametersAction in the build's actions array.
        Returns an empty dict if parameters are unavailable or the build has none.
        """
        try:
            info = self._server.get_build_info(job_name, build_number)
        except jenkins.JenkinsException as exc:
            logger.warning("Cannot fetch build parameters for %s #%d: %s", job_name, build_number, exc)
            return {}
        params: dict[str, str] = {}
        for action in info.get("actions", []):
            cls = action.get("_class", "")
            if "ParametersAction" in cls:
                for p in action.get("parameters", []):
                    name = p.get("name", "")
                    value = p.get("value", "")
                    if name:
                        params[name] = str(value) if value is not None else ""
        return params

    def get_build_url(self, job_name: str, build_number: int) -> str:
        """Construct the build URL."""
        return f"{self._url}/job/{job_name.replace('/', '/job/')}/{build_number}"

    def get_wfapi_stages(self, job_name: str, build_number: int) -> list[dict]:
        """Fetch authoritative stage list from Jenkins Workflow API.

        Returns a list of stage dicts with keys: id, name, status, error (optional).
        Falls back to empty list if wfapi is unavailable.
        """
        url = f"{self.get_build_url(job_name, build_number)}/wfapi/describe"
        try:
            resp = requests.get(url, auth=self._get_auth(), timeout=15, proxies={"http": None, "https": None})
            resp.raise_for_status()
            data = resp.json()
            return data.get("stages", [])
        except Exception as exc:
            logger.warning("wfapi unavailable for %s #%d: %s", job_name, build_number, exc)
            return []

    def get_stage_log(self, stage_node_id: str, job_name: str, build_number: int) -> str:
        """Fetch log for a specific pipeline stage node via wfapi.

        Because the stage-level node often has no direct log (length=0), we:
        1. Describe the stage node to find its child steps (stageFlowNodes).
        2. Collect logs from all failed/non-success steps.
        3. Fall back to the stage-level log if no step logs are found.
        """
        base_url = self.get_build_url(job_name, build_number)
        auth = self._get_auth()
        proxies = {"http": None, "https": None}

        def _fetch_node_log(node_id: str) -> str:
            url = f"{base_url}/execution/node/{node_id}/wfapi/log"
            try:
                resp = requests.get(url, auth=auth, timeout=15, proxies=proxies)
                resp.raise_for_status()
                return resp.json().get("text", "")
            except Exception as exc:
                logger.debug("Failed to fetch log for node %s: %s", node_id, exc)
                return ""

        # First try: describe the stage node and collect logs from its child steps.
        describe_url = f"{base_url}/execution/node/{stage_node_id}/wfapi/describe"
        try:
            resp = requests.get(describe_url, auth=auth, timeout=15, proxies=proxies)
            resp.raise_for_status()
            data = resp.json()
            flow_nodes = data.get("stageFlowNodes") or []

            # Collect logs from failed/non-success steps (prefer failed, include all if none failed).
            logs: list[str] = []
            failed_nodes = [n for n in flow_nodes if n.get("status") not in ("SUCCESS",)]
            for step in (failed_nodes or flow_nodes):
                step_log = _fetch_node_log(str(step["id"]))
                if step_log:
                    step_name = step.get("name", step["id"])
                    cmd_desc = step.get("parameterDescription", "")
                    header = f"[Step: {step_name}]"
                    if cmd_desc:
                        header += f"\n{cmd_desc.strip()}"
                    logs.append(f"{header}\n{step_log}")

            if logs:
                return "\n\n".join(logs)
        except Exception as exc:
            logger.warning("Failed to describe stage node %s: %s", stage_node_id, exc)

        # Fallback: try stage-level log directly.
        return _fetch_node_log(stage_node_id)

    def get_jenkinsfile(self, job_name: str, build_number: int) -> str:
        """Fetch the Jenkinsfile content for a build via the replay page.

        Returns the raw Groovy/Jenkinsfile script, or empty string if unavailable.
        """
        # Jenkins 302-redirects /replay → /replay/ ; allow_redirects handles it.
        url = f"{self.get_build_url(job_name, build_number)}/replay"
        try:
            resp = requests.get(
                url, auth=self._get_auth(), timeout=15,
                proxies={"http": None, "https": None},
                allow_redirects=True,
            )
            resp.raise_for_status()
            # The replay page embeds the script in a <textarea> element.
            m = re.search(r"<textarea[^>]*>(.*?)</textarea>", resp.text, re.DOTALL | re.IGNORECASE)
            if m:
                return _html_lib.unescape(m.group(1))
            logger.debug("No textarea found in replay page for %s #%d", job_name, build_number)
            return ""
        except Exception as exc:
            logger.warning("Failed to fetch Jenkinsfile for %s #%d: %s", job_name, build_number, exc)
            return ""

    def get_workspace_file(self, job_name: str, build_number: int, file_path: str) -> str:
        """Fetch a file from the Jenkins workspace for a specific build.

        Args:
            file_path: Relative path within the workspace (e.g. "ansible/deploy.yml").
        """
        build_url = self.get_build_url(job_name, build_number)
        # Try build-scoped workspace first, then job-scoped fallback.
        urls = [
            f"{build_url}/ws/{file_path}",
            f"{self._url}/job/{job_name.replace('/', '/job/')}/ws/{file_path}",
        ]
        for url in urls:
            try:
                resp = requests.get(
                    url, auth=self._get_auth(), timeout=15,
                    proxies={"http": None, "https": None},
                )
                if resp.status_code == 200 and "text/html" not in resp.headers.get("Content-Type", ""):
                    return resp.text
            except Exception:
                continue
        return ""

    def _get_auth(self) -> Optional[tuple[str, str]]:
        """Extract (username, password) tuple for use with requests.

        python-jenkins stores credentials in ``_auths`` as a list of
        (scheme, requests.auth.HTTPBasicAuth) tuples. Username/password may be
        bytes (when passed as str, python-jenkins encodes them).
        """
        try:
            for _scheme, auth_obj in getattr(self._server, "_auths", []):
                u = getattr(auth_obj, "username", None)
                p = getattr(auth_obj, "password", None)
                if u is not None:
                    # Decode bytes → str if needed.
                    u = u.decode() if isinstance(u, bytes) else u
                    p = p.decode() if isinstance(p, bytes) else (p or "")
                    return (u, p)
        except Exception:
            pass
        return None

    def parse_log(self, log_text: str) -> list[PipelineStageResult]:
        """Parse a Jenkins log into pipeline stage results."""
        stages = _identify_stages(log_text)
        if not stages:
            error_lines = _extract_error_lines(log_text)
            triggered = _extract_triggered_jobs(log_text)
            status = "fail" if error_lines else "unknown"
            return [
                PipelineStageResult(
                    stage=PipelineStage.UNKNOWN,
                    status=status,
                    error_summary="\n".join(error_lines[:50]) if error_lines else None,
                    log_excerpt=_tail_log(log_text, 200),
                    triggered_jobs=triggered,
                )
            ]
        return stages

    def get_triggered_job_log(self, job_name: str, build_number: int) -> Optional[str]:
        """Fetch the log of a triggered downstream job."""
        try:
            return self.get_build_log(job_name, build_number)
        except Exception:
            logger.warning("Could not fetch triggered job log for %s #%d", job_name, build_number)
            return None


def parse_log_text(log_text: str) -> list[PipelineStageResult]:
    """Parse raw Jenkins log text without requiring a Jenkins connection."""
    return _identify_stages(log_text) or [
        PipelineStageResult(
            stage=PipelineStage.UNKNOWN,
            status="fail" if _extract_error_lines(log_text) else "unknown",
            error_summary="\n".join(_extract_error_lines(log_text)[:50]) or None,
            log_excerpt=_tail_log(log_text, 200),
            triggered_jobs=_extract_triggered_jobs(log_text),
        )
    ]


_STAGE_ENTER_PATTERN = re.compile(r"\[Pipeline\]\s*\{\s*\(\s*(?P<name>[^)]+?)\s*\)")

# Pattern that extracts just the `#N` major number from a stage name.
_MAJOR_RE = re.compile(r"^\s*#(\d+)(?:\.\d+)?")


def _identify_stages(log_text: str) -> list[PipelineStageResult]:
    """Identify pipeline stages from Jenkins log text.

    Stage boundaries are driven purely by ``[Pipeline] { (<name>)`` markers.
    Stages are grouped by their top-level ``#N`` major number (e.g. all
    ``#3.x`` sub-stages form one block labelled by the ``#N`` header stage
    if present, otherwise by the first sub-stage).

    Non-numbered markers (``Branch: ...``, ``Declarative: ...``) attach to
    the currently-open block.

    Each resulting ``PipelineStageResult`` carries:
    - ``stage_name``: actual Jenkins stage name (e.g. ``#3 部署``)
    - ``stage``: best-effort PipelineStage enum (for backward compat only)
    - ``sub_stages``: all sub-stage names within the block
    - ``failing_sub_stage``: first sub-stage that contained errors
    - ``status``: ``pass`` / ``fail`` / ``skipped``
    """
    lines = log_text.splitlines()

    stage_entries: list[dict] = []
    for idx, line in enumerate(lines):
        m = _STAGE_ENTER_PATTERN.search(line)
        if m:
            stage_entries.append({"line_idx": idx, "name": m.group("name").strip()})

    if not stage_entries:
        return []

    # -----------------------------------------------------------------------
    # Group entries into blocks by ``#N`` major number.
    #   - Numbered entries (#N, #N.x) → group by major N
    #   - Non-numbered entries attach to the current open block
    # -----------------------------------------------------------------------
    blocks: list[dict] = []

    def _major(name: str) -> Optional[str]:
        m = _MAJOR_RE.match(name)
        return m.group(1) if m else None

    for entry in stage_entries:
        name = entry["name"]
        major = _major(name)

        if major is None:
            # Decide whether to attach to current block or start a new one.
            # Markers that are "internal" pipeline infra (Branch: …, Declarative: …)
            # attach to the current block as a sub-stage annotation.
            # All other named markers (e.g. "Preparation", "Deploy", "Test") are
            # real top-level stages and start their own block.
            is_internal = re.match(r"^(?:Branch\s*:|Declarative\s*:)", name, re.IGNORECASE)
            if is_internal and blocks:
                blocks[-1]["sub_stages"].append(name)
                blocks[-1]["end_line"] = entry["line_idx"]
                continue
            # Real non-numbered top-level stage — new block every time.
            blocks.append({
                "major": None,
                "sub_stages": [name],
                "start_line": entry["line_idx"],
                "end_line": entry["line_idx"],
            })
            continue

        # Numbered marker — start a new block when major changes.
        if blocks and blocks[-1].get("major") == major:
            blocks[-1]["sub_stages"].append(name)
            blocks[-1]["end_line"] = entry["line_idx"]
        else:
            blocks.append({
                "major": major,
                "sub_stages": [name],
                "start_line": entry["line_idx"],
                "end_line": entry["line_idx"],
            })

    if not blocks:
        return []

    # Assign body line ranges.
    for i, block in enumerate(blocks):
        start = block["start_line"]
        end = blocks[i + 1]["start_line"] if i + 1 < len(blocks) else len(lines)
        block["body_lines"] = lines[start:end]

    results: list[PipelineStageResult] = []
    claimed_jobs: set[tuple[str, int]] = set()

    for block in blocks:
        body = "\n".join(block["body_lines"])
        error_lines = _extract_error_lines(body)
        skipped = bool(_SKIPPED_STAGE_PATTERN.search(body)) and not error_lines
        triggered = _extract_triggered_jobs(body, exclude=claimed_jobs)
        for job in triggered:
            claimed_jobs.add((job.job_name, job.build_number))

        if skipped:
            status = "skipped"
        elif error_lines:
            status = "fail"
        else:
            status = "pass"

        # Derive the human-readable stage name for this block:
        # prefer the sub-stage whose name matches `#N<space>` (no decimal),
        # otherwise use the first sub-stage.
        stage_name = _derive_block_name(block["major"], block["sub_stages"])

        result = PipelineStageResult(
            stage_name=stage_name,
            stage=_classify_stage_type(stage_name),
            status=status,
            error_summary="\n".join(error_lines[:30]) if error_lines else None,
            log_excerpt=_tail_log(body, 100),
            triggered_jobs=triggered,
            sub_stages=block["sub_stages"],
            failing_sub_stage=_locate_failing_sub_stage(block["body_lines"], block["sub_stages"]),
        )
        results.append(result)

    return results


def _derive_block_name(major: Optional[str], sub_stages: list[str]) -> str:
    """Return the best human-readable name for a block.

    Prefers a sub-stage that looks like a top-level ``#N name`` (no decimal),
    otherwise falls back to the first sub-stage name.
    """
    if not sub_stages:
        return "其他"
    if major is not None:
        # Look for an entry like "#3 部署" (no decimal point after major).
        for name in sub_stages:
            m = _MAJOR_RE.match(name)
            if m and m.group(1) == major:
                rest = name[m.end():]
                if not rest.startswith("."):
                    return name.strip()
    return sub_stages[0].strip()


# ---------------------------------------------------------------------------
# Legacy best-effort line classifier kept for backwards compatibility with
# external callers and unit tests. NOT used by the primary stage parser — it
# matches tool-invocation lines too (e.g. ``kubectl apply``) which would cause
# false stage transitions when run over a full log.
# ---------------------------------------------------------------------------
_LEGACY_STAGE_PATTERNS: dict[PipelineStage, list[re.Pattern[str]]] = {
    PipelineStage.PREPARATION: [
        re.compile(r"\[Pipeline\]\s*\{\s*\((?:准备|Preparation|Setup|Environment|Init)", re.IGNORECASE),
        re.compile(r"(?:stage|阶段).*?(?:准备|preparation|setup|environment|init)", re.IGNORECASE),
        re.compile(r"(?:checkout|clone|pull|fetch)\s+(?:scm|git|repo)", re.IGNORECASE),
        re.compile(r"Installing\s+dependencies", re.IGNORECASE),
    ],
    PipelineStage.DEPLOYMENT: [
        re.compile(r"\[Pipeline\]\s*\{\s*\((?:部署|Deploy|Update|Rollout)", re.IGNORECASE),
        re.compile(r"(?:stage|阶段).*?(?:部署|deploy|update|rollout|release)", re.IGNORECASE),
        re.compile(r"(?:kubectl|helm)\s+(?:apply|deploy|upgrade|push)", re.IGNORECASE),
    ],
    PipelineStage.TESTING: [
        re.compile(r"\[Pipeline\]\s*\{\s*\((?:测试|Test|QA|Verify|Validation|#\d+(?:\.\d+)?\s*执行测试)", re.IGNORECASE),
        re.compile(r"(?:stage|阶段).*?(?:测试|test|qa|verify|validation)", re.IGNORECASE),
        re.compile(r"(?:pytest|jest|mvn\s+test|gradle\s+test|robot|allure)", re.IGNORECASE),
    ],
}


def _detect_stage(line: str) -> Optional[PipelineStage]:
    """Best-effort line classifier (legacy API, not used by parser)."""
    for stage, patterns in _LEGACY_STAGE_PATTERNS.items():
        for pat in patterns:
            if pat.search(line):
                return stage
    return None


def _locate_failing_sub_stage(body_lines: list[str], sub_stages: list[str]) -> Optional[str]:
    """Find the sub-stage name whose body contains the first real error."""
    if not sub_stages:
        return None
    # Walk through body lines, tracking the currently active sub-stage and
    # returning the one that first contains a real error signal.
    current = sub_stages[0]
    for line in body_lines:
        m = _STAGE_ENTER_PATTERN.search(line)
        if m:
            candidate = m.group("name").strip()
            if candidate in sub_stages:
                current = candidate
            continue
        if _is_noise_line(line):
            continue
        if any(pat.search(line) for pat in _ERROR_PATTERNS):
            return current
    return None


def _is_noise_line(line: str) -> bool:
    return any(pat.search(line) for pat in _ERROR_NOISE_PATTERNS)


def _extract_error_lines(text: str) -> list[str]:
    """Extract lines that match error patterns, filtering noise."""
    errors: list[str] = []
    for line in text.splitlines():
        if _is_noise_line(line):
            continue
        for pat in _ERROR_PATTERNS:
            if pat.search(line):
                errors.append(line.strip())
                break
    return errors


def _extract_triggered_jobs(
    text: str,
    exclude: Optional[set[tuple[str, int]]] = None,
) -> list[TriggeredJobInfo]:
    """Extract triggered downstream job information from log text.

    ``exclude`` lets the caller prevent jobs already claimed by an earlier
    stage from being re-attached to a later one.
    """
    exclude = exclude or set()
    jobs: dict[tuple[str, int], TriggeredJobInfo] = {}

    for line in text.splitlines():
        for pat in _TRIGGERED_JOB_START_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            name = m.group("job").strip().strip("'\"").rstrip(":")
            if not _is_valid_job_name(name):
                continue
            number = int(m.group("num"))
            key = (name, number)
            if key in exclude:
                continue
            jobs.setdefault(key, TriggeredJobInfo(job_name=name, build_number=number, status="unknown"))

    for match in _TRIGGERED_JOB_RESULT_PATTERN.finditer(text):
        name = match.group("job").strip().strip("'\"").rstrip(":")
        if not _is_valid_job_name(name):
            continue
        number = int(match.group("num"))
        result = match.group("status").lower()
        key = (name, number)
        if key in exclude:
            continue
        if key in jobs:
            jobs[key].status = result
        # Only create a new entry from a completion line if we already know
        # about the job *or* it is not in the exclude set. Otherwise we risk
        # re-introducing cascade-failure duplicates.
        elif key not in exclude:
            jobs[key] = TriggeredJobInfo(job_name=name, build_number=number, status=result)

    return list(jobs.values())


def _is_valid_job_name(name: str) -> bool:
    """Filter out reserved/parser-artifact tokens like 'building:'."""
    if not name:
        return False
    lowered = name.lower().strip()
    return lowered not in _RESERVED_JOB_TOKENS



def _tail_log(text: str, max_lines: int) -> str:
    """Return the last N lines of a log."""
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])

