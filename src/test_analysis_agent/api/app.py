"""FastAPI service layer for the Test Analysis Agent.

Provides HTTP API endpoints for:
- Triggering pipeline failure analysis
- Analyzing raw log text
- Managing skills
- Health checks
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from test_analysis_agent import __version__
from test_analysis_agent.agent import AnalysisAgent
from test_analysis_agent.config import get_settings
from test_analysis_agent.feishu_reporter import create_feishu_report, list_feishu_reports
from test_analysis_agent.models.schemas import AnalysisReport, AnalysisRequest
from test_analysis_agent.report_generator import (
    generate_html_report,
    generate_json_report,
    generate_markdown_report,
)

logger = logging.getLogger(__name__)

# Clear system proxy — all services (Jenkins, Allure, Feishu, LLM) are reachable directly.
for _proxy_var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_proxy_var, None)

app = FastAPI(
    title="Test Analysis Agent",
    description="AI-powered CD pipeline failure analysis service",
    version=__version__,
)

_agent: Optional[AnalysisAgent] = None


def get_agent() -> AnalysisAgent:
    """Get or create the analysis agent singleton."""
    global _agent
    if _agent is None:
        _agent = AnalysisAgent(get_settings())
    return _agent


# ─── Request/Response Models ────────────────────────────────────────


class AnalyzeLogRequest(BaseModel):
    """Request to analyze raw log text."""

    log_text: str = Field(description="Raw Jenkins log text to analyze")
    job_name: str = Field(default="unknown", description="Job name for the report")
    build_number: int = Field(default=0, description="Build number for the report")


class ReportFormatRequest(BaseModel):
    """Request specifying report format."""

    format: str = Field(default="markdown", description="Report format: markdown, json, or html")


class SkillInfo(BaseModel):
    """Skill information."""

    name: str
    description: str
    version: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    skills_count: int


# ─── API Endpoints ──────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    agent = get_agent()
    return HealthResponse(
        status="healthy",
        version=__version__,
        skills_count=len(agent.skills.list_skills()),
    )


@app.post("/api/v1/analyze", response_model=AnalysisReport)
async def analyze_pipeline(request: AnalysisRequest) -> AnalysisReport:
    """Analyze a Jenkins pipeline failure.

    Fetches the build log from Jenkins, identifies the failure stage,
    and runs AI-powered analysis to identify root causes.
    """
    agent = get_agent()
    try:
        report = agent.analyze(request)
        return report
    except Exception as exc:
        logger.exception("Analysis failed for %s #%d", request.job_name, request.build_number)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


@app.post("/api/v1/analyze/log", response_model=AnalysisReport)
async def analyze_log(request: AnalyzeLogRequest) -> AnalysisReport:
    """Analyze raw log text directly.

    Useful when the log is already available and no Jenkins connection is needed.
    """
    agent = get_agent()
    try:
        report = agent.analyze_log_text(
            log_text=request.log_text,
            job_name=request.job_name,
            build_number=request.build_number,
        )
        return report
    except Exception as exc:
        logger.exception("Log analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


@app.post("/api/v1/analyze/report/{format}")
async def analyze_pipeline_with_format(request: AnalysisRequest, format: str = "markdown") -> dict:
    """Analyze a pipeline failure and return the report in the specified format.

    Supported formats: markdown, json, html
    """
    agent = get_agent()
    try:
        report = agent.analyze(request)
        return _format_report(report, format)
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


@app.post("/api/v1/analyze/log/report/{format}")
async def analyze_log_with_format(request: AnalyzeLogRequest, format: str = "markdown") -> dict:
    """Analyze raw log text and return the report in the specified format."""
    agent = get_agent()
    try:
        report = agent.analyze_log_text(
            log_text=request.log_text,
            job_name=request.job_name,
            build_number=request.build_number,
        )
        return _format_report(report, format)
    except Exception as exc:
        logger.exception("Log analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


@app.get("/api/v1/skills", response_model=list[SkillInfo])
async def list_skills() -> list[SkillInfo]:
    """List all registered analysis skills."""
    agent = get_agent()
    return [SkillInfo(**s) for s in agent.skills.list_skills()]


@app.post("/api/v1/report/feishu")
async def create_feishu_document(report: AnalysisReport) -> dict:
    """Create a Feishu cloud document from an analysis report.

    Accepts the JSON output of the /api/v1/analyze endpoint and creates
    a structured, human-readable Feishu cloud document containing:
    - Module version information
    - Test case results (pass/fail counts and failure details)
    - Analyzed issues
    - Bug filing recommendations

    Returns the URL of the created Feishu document.
    """
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(
            status_code=503,
            detail="Feishu integration not configured. Set TAA_FEISHU_APP_ID and TAA_FEISHU_APP_SECRET.",
        )
    try:
        doc_url = await create_feishu_report(
            report, settings.feishu_app_id, settings.feishu_app_secret,
            folder_token=settings.feishu_folder_token,
        )
        return {"document_url": doc_url}
    except Exception as exc:
        logger.exception("Failed to create Feishu document")
        raise HTTPException(status_code=500, detail=f"Feishu document creation failed: {exc}") from exc


@app.get("/api/v1/reports/feishu")
async def list_feishu_documents() -> dict:
    """List all Feishu cloud documents created by this service.

    Returns a list of documents with their names, URLs, and creation times.
    """
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(
            status_code=503,
            detail="Feishu integration not configured.",
        )
    try:
        documents = await list_feishu_reports(
            settings.feishu_app_id, settings.feishu_app_secret,
            folder_token=settings.feishu_folder_token,
        )
        return {"documents": documents, "count": len(documents)}
    except Exception as exc:
        logger.exception("Failed to list Feishu documents")
        raise HTTPException(status_code=500, detail=f"Failed to list Feishu documents: {exc}") from exc


def _format_report(report: AnalysisReport, format: str) -> dict:
    """Format an analysis report in the requested format."""
    if format == "json":
        return {"format": "json", "content": generate_json_report(report)}
    elif format == "html":
        return {"format": "html", "content": generate_html_report(report)}
    elif format == "markdown":
        return {"format": "markdown", "content": generate_markdown_report(report)}
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}. Use markdown, json, or html.")
