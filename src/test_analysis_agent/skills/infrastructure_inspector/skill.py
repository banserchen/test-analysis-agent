"""Infrastructure inspector skill.

When log analysis reveals connection errors (ConnectionRefusedError,
ClientConnectorError, etc.) to a specific IP:port, this skill:

1. Queries the environments API to find SSH credentials for that host.
2. SSH into the host and identifies which docker container serves the port.
3. Checks container status and compares its start time with the build window.
4. Calls LLM (via prompt.md) to interpret findings and generate a structured issue.

This provides automated "why can't we connect?" investigation without manual
SSH access by the analyst.

Requirements: paramiko (optional — skill silently skips if not installed).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from test_analysis_agent.skills.base import LLMBaseSkill

logger = logging.getLogger(__name__)

# Connection error patterns with IP:port capture
_CONN_ERROR_PATTERNS: list[re.Pattern[str]] = [
    # aiohttp: Cannot connect to host 192.168.109.158:10000
    re.compile(
        r"Cannot connect to host\s+(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})",
        re.IGNORECASE,
    ),
    # ConnectionRefusedError: [Errno 111] Connect call failed ('192.168.109.158', 10000)
    re.compile(
        r"[Cc]onnect(?:ion)? (?:refused|call failed)[^\d]*\(?['\"]?(\d{1,3}(?:\.\d{1,3}){3})['\"]?,\s*(\d{2,5})",
    ),
    # requests/httpx: Connection refused: 192.168.109.158:10000
    re.compile(
        r"[Cc]onnection refused.*?(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})",
    ),
    # ClientConnectorError ... host='192.168.109.158' port=10000
    re.compile(
        r"host=['\"]?(\d{1,3}(?:\.\d{1,3}){3})['\"]?.*?port=(\d{2,5})",
    ),
]

# Severity threshold: if the service was down during the ENTIRE build, it's critical
_CRITICAL_DOWNTIME_RATIO = 0.5  # container started after >50% of build elapsed → critical


def _import_paramiko() -> Optional[Any]:
    """Lazily import paramiko; return None if not installed."""
    try:
        import paramiko  # type: ignore

        return paramiko
    except ImportError:
        return None


def _extract_failing_endpoints(log_text: str) -> list[tuple[str, int]]:
    """Extract unique (ip, port) pairs from connection error lines.

    Loopback (127.x.x.x) and unroutable (0.0.0.0) addresses are excluded
    because they refer to the local host, not a remote service under investigation.
    """
    _SKIP_PREFIXES = ("127.", "0.0.0.")

    endpoints: set[tuple[str, int]] = set()
    for line in log_text.splitlines():
        for pat in _CONN_ERROR_PATTERNS:
            m = pat.search(line)
            if m:
                try:
                    ip = m.group(1)
                    port = int(m.group(2))
                    if any(ip.startswith(pfx) for pfx in _SKIP_PREFIXES):
                        continue
                    endpoints.add((ip, port))
                except (IndexError, ValueError):
                    pass
    return list(endpoints)


def _get_env_credentials(ip: str, api_url: str) -> Optional[dict[str, str]]:
    """Fetch environment list from the platform API and find SSH creds for *ip*."""
    try:
        url = api_url.rstrip("/") + "/api/environments"
        req = urllib.request.Request(url)
        # Bypass system proxy — the environments API is on the internal network
        no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with no_proxy_opener.open(req, timeout=8) as resp:  # noqa: S310
            envs: list[dict] = json.loads(resp.read())
        for env in envs:
            env_name = env.get("name", "")
            for fms in env.get("fms", []):
                if fms.get("ip") == ip:
                    return {
                        "user": fms.get("user", ""),
                        "password": fms.get("password", ""),
                        "env_name": env_name,
                        "role": "fms",
                    }
            for pp in env.get("pp", []):
                if pp.get("ip") == ip:
                    return {
                        "user": pp.get("user", ""),
                        "password": pp.get("password", ""),
                        "env_name": env_name,
                        "role": "pp",
                    }
    except Exception as exc:  # noqa: BLE001
        logger.warning("infrastructure_inspector: failed to fetch environments: %s", exc)
    return None


def _ssh_exec(client: Any, cmd: str) -> str:
    """Execute a command over SSH and return stdout as a string."""
    _, stdout, _ = client.exec_command(cmd)
    return stdout.read().decode(errors="replace")


def _inspect_port(client: Any, ip: str, port: int, password: str) -> dict[str, Any]:
    """SSH-based investigation of which container serves *port* on the remote host."""
    result: dict[str, Any] = {
        "port": port,
        "host": ip,
        "listening": False,
        "container_name": None,
        "container_status": None,
        "container_started_at": None,
        "container_restart_count": None,
        "accessible_locally": False,
        "error": None,
    }

    try:
        # 1. Check if port is listening (no sudo needed for ss)
        ss_out = _ssh_exec(client, f"ss -tlnp | grep :{port}")
        if not ss_out.strip():
            result["error"] = f"Port {port} is not listening on {ip}"
            return result
        result["listening"] = True

        # 2. Find PID owning the port (needs sudo)
        ss_sudo = _ssh_exec(
            client, f"echo {password} | sudo -S ss -tlnp | grep :{port}"
        )
        pid_match = re.search(r"pid=(\d+)", ss_sudo)
        if not pid_match:
            result["error"] = "Cannot determine PID for port (sudo may be required)"
            return result
        pid = pid_match.group(1)

        # 3. Map PID → docker container via cgroup
        cgroup = _ssh_exec(
            client, f"echo {password} | sudo -S cat /proc/{pid}/cgroup"
        )
        cid_match = re.search(r"docker-([a-f0-9]{32,})", cgroup)
        if not cid_match:
            result["error"] = f"PID {pid} is not running inside a docker container"
            return result
        container_id = cid_match.group(1)[:12]

        # 4. Inspect container metadata
        inspect_raw = _ssh_exec(
            client,
            (
                f"docker inspect {container_id}"
                ' --format "{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}"'
            ),
        )
        parts = inspect_raw.strip().split("|")
        if len(parts) >= 4:
            result["container_name"] = parts[0].lstrip("/")
            result["container_status"] = parts[1]
            result["container_started_at"] = parts[2]
            result["container_restart_count"] = int(parts[3]) if parts[3].isdigit() else 0

        # 5. Quick local curl test
        curl_out = _ssh_exec(
            client,
            f"curl -s --max-time 3 http://127.0.0.1:{port}/ -o /dev/null -w '%{{http_code}}' 2>/dev/null",
        )
        result["accessible_locally"] = curl_out.strip() not in ("", "000")

    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)

    return result


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string, return UTC-aware datetime or None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_timing_analysis(
    container_started_at: str,
    build_info: dict[str, Any],
) -> tuple[str, str, str, str]:
    """Compute build window strings and a plain-text timing analysis.

    Returns (build_start_str, build_end_str, duration_min_str, timing_analysis).
    """
    build_start_ms: int = build_info.get("timestamp", 0)
    build_duration_ms: int = build_info.get("duration", 0)

    if not build_start_ms:
        return "未知", "未知", "未知", ""

    build_start = datetime.fromtimestamp(build_start_ms / 1000, tz=timezone.utc)
    build_end = build_start + timedelta(milliseconds=build_duration_ms)
    duration_min = f"{build_duration_ms / 60000:.0f}"

    c_started = _parse_iso(container_started_at) if container_started_at else None
    if c_started is None:
        return (
            build_start.strftime("%Y-%m-%d %H:%M:%S"),
            build_end.strftime("%Y-%m-%d %H:%M:%S"),
            duration_min,
            "",
        )

    if c_started > build_end:
        delay_min = (c_started - build_end).total_seconds() / 60
        analysis = (
            f"容器在构建结束后 {delay_min:.0f} 分钟才启动"
            f"（容器: {c_started.strftime('%H:%M:%S')} UTC，构建结束: {build_end.strftime('%H:%M:%S')} UTC）。"
            f"测试运行期间该服务完全未运行。"
        )
    elif c_started > build_start:
        elapsed_pct = (c_started - build_start).total_seconds() / max(build_duration_ms / 1000, 1) * 100
        analysis = (
            f"容器在构建进行 {elapsed_pct:.0f}% 时才启动"
            f"（容器: {c_started.strftime('%H:%M:%S')} UTC，构建开始: {build_start.strftime('%H:%M:%S')} UTC）。"
            f"测试开始时服务可能尚未就绪。"
        )
    else:
        analysis = (
            f"容器早于构建开始前已运行（容器启动: {c_started.strftime('%H:%M:%S')} UTC，"
            f"构建开始: {build_start.strftime('%H:%M:%S')} UTC）。"
            f"服务时序上应处于就绪状态，连接失败可能来自内部异常或网络问题。"
        )

    return (
        build_start.strftime("%Y-%m-%d %H:%M:%S"),
        build_end.strftime("%Y-%m-%d %H:%M:%S"),
        duration_min,
        analysis,
    )


def _extract_conn_evidence(log_text: str, ip: str, port: int, max_lines: int = 5) -> str:
    """Extract up to *max_lines* log lines that reference the failing endpoint."""
    evidence: list[str] = []
    for line in log_text.splitlines():
        if ip in line or str(port) in line:
            stripped = line.strip()
            if stripped and any(
                kw in line.lower()
                for kw in ("connect", "refused", "timeout", "error", "failed")
            ):
                evidence.append(stripped)
                if len(evidence) >= max_lines:
                    break
    return "\n".join(evidence) or f"(连接到 {ip}:{port} 时出现错误)"


class InfrastructureInspectorSkill(LLMBaseSkill):
    """SSH-based infrastructure investigation for connection failures.

    When logs contain ConnectionRefusedError or connect timeout to a specific
    IP:port, this skill queries the environments API for SSH credentials,
    connects to the target host, identifies the responsible docker container,
    and calls LLM (via prompt.md) to interpret the findings.
    """

    name = "infrastructure_inspector"
    description = "SSH 调查连接失败的服务器容器状态（LLM 解读）"
    version = "2.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        log_text: str = context.get("log_text", "")
        environments_api_url: str = context.get("environments_api_url", "")
        if not environments_api_url:
            return False
        if _import_paramiko() is None:
            logger.debug("infrastructure_inspector: paramiko not installed, skipping")
            return False
        return len(_extract_failing_endpoints(log_text)) > 0

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        log_text: str = context.get("log_text", "")
        environments_api_url: str = context.get("environments_api_url", "")
        build_info: dict = context.get("build_info") or {}

        endpoints = _extract_failing_endpoints(log_text)
        analyzed_issues: list[dict] = []

        paramiko = _import_paramiko()
        if paramiko is None:
            return {"analyzed_issues": analyzed_issues}

        for ip, port in endpoints:
            logger.info("infrastructure_inspector: investigating %s:%d", ip, port)

            creds = _get_env_credentials(ip, environments_api_url)
            if not creds:
                logger.warning(
                    "infrastructure_inspector: no credentials found for %s", ip
                )
                continue

            user = creds["user"]
            password = creds["password"]
            env_name = creds.get("env_name", ip)
            role = creds.get("role", "fms")

            # SSH investigation
            ssh_error: Optional[str] = None
            container_info: dict[str, Any] = {}

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(ip, username=user, password=password, timeout=15)
                container_info = _inspect_port(client, ip, port, password)
                logger.info(
                    "infrastructure_inspector: container '%s' status=%s for %s:%d",
                    container_info.get("container_name"),
                    container_info.get("container_status"),
                    ip, port,
                )
            except Exception as exc:  # noqa: BLE001
                ssh_error = str(exc)
                logger.error("infrastructure_inspector: SSH to %s failed: %s", ip, exc)
            finally:
                client.close()

            # Build timing analysis
            build_start, build_end, duration_min, timing_analysis = _build_timing_analysis(
                container_info.get("container_started_at", ""),
                build_info,
            )

            # Collect log evidence for this endpoint
            conn_error_evidence = _extract_conn_evidence(log_text, ip, port)

            # Call LLM to interpret findings
            prompt = self.render_prompt(
                ip=ip,
                port=port,
                env_name=env_name,
                role=role,
                conn_error_evidence=conn_error_evidence,
                ssh_error=ssh_error,
                listening=container_info.get("listening", False),
                container_name=container_info.get("container_name"),
                container_status=container_info.get("container_status"),
                container_started_at=container_info.get("container_started_at", ""),
                restart_count=container_info.get("container_restart_count", 0),
                accessible_locally=container_info.get("accessible_locally", False),
                container_error=container_info.get("error"),
                build_start=build_start,
                build_end=build_end,
                build_duration_min=duration_min,
                timing_analysis=timing_analysis,
            )

            if not prompt:
                logger.warning("infrastructure_inspector: prompt template not loaded")
                continue

            llm_response = self.call_llm(prompt)
            if not llm_response:
                logger.warning("infrastructure_inspector: LLM returned no response")
                continue

            issue = _parse_llm_issue(llm_response, ip, port, container_info)
            analyzed_issues.append(issue)

        return {"analyzed_issues": analyzed_issues}


def _parse_llm_issue(
    llm_response: str,
    ip: str,
    port: int,
    container_info: dict[str, Any],
) -> dict[str, Any]:
    """Parse LLM JSON response into an analyzed_issue dict."""
    from test_analysis_agent.llm_analyzer import _parse_json_response  # local import

    parsed = _parse_json_response(llm_response) or {}

    container_name = container_info.get("container_name") or "unknown"
    container_started = container_info.get("container_started_at", "")

    return {
        "title": parsed.get("title", f"服务 {ip}:{port} 连接失败"),
        "description": parsed.get(
            "description",
            f"测试日志中检测到对 {ip}:{port}（容器: {container_name}）的连接失败，SSH 调查已完成。",
        ),
        "category": parsed.get("category", "deployment_error"),
        "severity": parsed.get("severity", "high"),
        "stage_name": "",
        "affected_tests": [],
        "root_cause": parsed.get("root_cause", ""),
        "suggestion": parsed.get("suggestion", ""),
        "should_file_bug": bool(parsed.get("should_file_bug", False)),
        "bug_summary": parsed.get("bug_summary"),
        "log_evidence": (
            f"container={container_name}, started={container_started[:19] if container_started else 'N/A'}, "
            f"status={container_info.get('container_status', 'unknown')}"
        ),
    }
