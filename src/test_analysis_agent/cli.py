"""CLI interface for the Test Analysis Agent.

Provides command-line access for:
- Analyzing Jenkins pipeline failures
- Analyzing log files directly
- Starting the API server
- Managing skills
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from test_analysis_agent import __version__
from test_analysis_agent.agent import AnalysisAgent
from test_analysis_agent.config import Settings
from test_analysis_agent.models.schemas import AnalysisRequest
from test_analysis_agent.report_generator import (
    generate_html_report,
    generate_json_report,
    generate_markdown_report,
)

console = Console()


def _setup_logging(verbose: bool) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _create_agent(ctx: click.Context) -> AnalysisAgent:
    """Create an analysis agent from CLI context."""
    settings = Settings()
    # Override settings from CLI options if provided
    if ctx.obj.get("jenkins_url"):
        settings.jenkins_url = ctx.obj["jenkins_url"]
    if ctx.obj.get("jenkins_user"):
        settings.jenkins_username = ctx.obj["jenkins_user"]
    if ctx.obj.get("jenkins_password"):
        settings.jenkins_password = ctx.obj["jenkins_password"]
    if ctx.obj.get("model"):
        settings.llm_model = ctx.obj["model"]
    if ctx.obj.get("language"):
        settings.report_language = ctx.obj["language"]
    return AnalysisAgent(settings)


@click.group()
@click.version_option(version=__version__, prog_name="test-analysis-agent")
@click.option("--jenkins-url", envvar="TAA_JENKINS_URL", help="Jenkins server URL")
@click.option("--jenkins-user", envvar="TAA_JENKINS_USERNAME", help="Jenkins username")
@click.option("--jenkins-password", envvar="TAA_JENKINS_PASSWORD", help="Jenkins password/token")
@click.option("--model", envvar="TAA_LLM_MODEL", help="LLM model name")
@click.option("--language", envvar="TAA_REPORT_LANGUAGE", default="zh-CN", help="Report language (zh-CN, en)")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def main(
    ctx: click.Context,
    jenkins_url: Optional[str],
    jenkins_user: Optional[str],
    jenkins_password: Optional[str],
    model: Optional[str],
    language: str,
    verbose: bool,
) -> None:
    """Test Analysis Agent - AI-powered CD pipeline failure analysis.

    Analyzes Jenkins pipeline failures, Allure test reports, and provides
    actionable insights with bug filing recommendations.
    """
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["jenkins_url"] = jenkins_url
    ctx.obj["jenkins_user"] = jenkins_user
    ctx.obj["jenkins_password"] = jenkins_password
    ctx.obj["model"] = model
    ctx.obj["language"] = language


@main.command()
@click.argument("job_name")
@click.argument("build_number", type=int)
@click.option("--allure-url", help="URL to Allure report")
@click.option("--allure-path", help="Local path to Allure report directory")
@click.option("--test-repo", help="Path to test source code repository")
@click.option("--format", "output_format", default="markdown", type=click.Choice(["markdown", "json", "html"]))
@click.option("-o", "--output", "output_file", help="Output file path (default: stdout)")
@click.pass_context
def analyze(
    ctx: click.Context,
    job_name: str,
    build_number: int,
    allure_url: Optional[str],
    allure_path: Optional[str],
    test_repo: Optional[str],
    output_format: str,
    output_file: Optional[str],
) -> None:
    """Analyze a Jenkins pipeline failure.

    JOB_NAME: Full Jenkins job name (e.g., folder/job-name)
    BUILD_NUMBER: Jenkins build number to analyze
    """
    agent = _create_agent(ctx)

    request = AnalysisRequest(
        job_name=job_name,
        build_number=build_number,
        allure_report_url=allure_url,
        allure_report_path=allure_path,
        test_repo_path=test_repo,
    )

    with console.status("[bold green]Analyzing pipeline failure..."):
        report = agent.analyze(request)

    # Generate output
    content = _format_output(report, output_format)
    _write_output(content, output_file)

    # Print summary to console
    _print_summary(report)


@main.command(name="analyze-log")
@click.argument("log_file", type=click.Path(exists=True))
@click.option("--job-name", default="unknown", help="Job name for the report")
@click.option("--build-number", default=0, type=int, help="Build number for the report")
@click.option("--format", "output_format", default="markdown", type=click.Choice(["markdown", "json", "html"]))
@click.option("-o", "--output", "output_file", help="Output file path (default: stdout)")
@click.pass_context
def analyze_log(
    ctx: click.Context,
    log_file: str,
    job_name: str,
    build_number: int,
    output_format: str,
    output_file: Optional[str],
) -> None:
    """Analyze a Jenkins log file directly.

    LOG_FILE: Path to the Jenkins console output log file
    """
    agent = _create_agent(ctx)

    log_text = Path(log_file).read_text(encoding="utf-8")

    with console.status("[bold green]Analyzing log file..."):
        report = agent.analyze_log_text(log_text, job_name, build_number)

    content = _format_output(report, output_format)
    _write_output(content, output_file)
    _print_summary(report)


@main.command()
@click.option("--host", default="0.0.0.0", help="API server host")
@click.option("--port", default=9090, type=int, help="API server port")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the API server.

    Runs the FastAPI application for HTTP API access.
    """
    import os
    import uvicorn

    # Clear system proxy — all services (Jenkins, Allure, Feishu, LLM) are reachable directly.
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(var, None)

    console.print(Panel(
        f"[bold green]Test Analysis Agent API Server[/bold green]\n"
        f"Version: {__version__}\n"
        f"Listening on: http://{host}:{port}\n"
        f"API docs: http://{host}:{port}/docs",
        title="🚀 Starting Server",
    ))

    uvicorn.run(
        "test_analysis_agent.api.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@main.command()
@click.pass_context
def skills(ctx: click.Context) -> None:
    """List all registered analysis skills."""
    agent = _create_agent(ctx)
    skill_list = agent.skills.list_skills()

    if not skill_list:
        console.print("[yellow]No skills registered.[/yellow]")
        return

    table = Table(title="Registered Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="green")
    table.add_column("Version", style="yellow")

    for skill in skill_list:
        table.add_row(skill["name"], skill["description"], skill["version"])

    console.print(table)


def _format_output(report, output_format: str) -> str:
    """Format the report in the requested format."""
    if output_format == "json":
        return generate_json_report(report)
    elif output_format == "html":
        return generate_html_report(report)
    else:
        return generate_markdown_report(report)


def _write_output(content: str, output_file: Optional[str]) -> None:
    """Write content to file or stdout."""
    if output_file:
        Path(output_file).write_text(content, encoding="utf-8")
        console.print(f"[green]Report written to {output_file}[/green]")
    else:
        click.echo(content)


def _print_summary(report) -> None:
    """Print a brief summary to console."""
    console.print()
    console.print(Panel(
        f"[bold]Job:[/bold] {report.job_name} #{report.build_number}\n"
        f"[bold]Status:[/bold] {report.overall_status}\n"
        f"[bold]Failure Stage:[/bold] {report.failure_stage.value}\n"
        f"[bold]Issues Found:[/bold] {len(report.issues)}\n"
        f"[bold]Test Failures:[/bold] {len(report.test_failures)}\n"
        f"[bold]Bugs to File:[/bold] {sum(1 for i in report.issues if i.should_file_bug)}",
        title="📊 Analysis Summary",
        border_style="blue",
    ))


if __name__ == "__main__":
    main()
