"""Feishu (Lark) cloud document reporter.

Creates a structured, human-readable Feishu cloud document from an AnalysisReport.
Uses the Feishu docx API (v1) to build headings, bullet lists, and tables.

Main entry point:
    url = await create_feishu_report(report, app_id, app_secret)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from test_analysis_agent.models.schemas import (
    AnalysisReport,
    AnalyzedIssue,
    BugRecommendation,
    Severity,
)

logger = logging.getLogger(__name__)

_FEISHU_BASE = "https://open.feishu.cn/open-apis"

# ─── Block type constants ────────────────────────────────────────────
_BT_TEXT = 2
_BT_H1 = 3
_BT_H2 = 4
_BT_H3 = 5
_BT_BULLET = 12
_BT_TABLE = 31


# ─── Auth ────────────────────────────────────────────────────────────


async def _get_tenant_access_token(client: httpx.AsyncClient, app_id: str, app_secret: str) -> str:
    resp = await client.post(
        f"{_FEISHU_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {data}")
    return data["tenant_access_token"]


# ─── Block builders ──────────────────────────────────────────────────


def _text_run(content: str, *, bold: bool = False, italic: bool = False, color: Optional[str] = None) -> dict:
    style: dict[str, Any] = {}
    if bold:
        style["bold"] = True
    if italic:
        style["italic"] = True
    if color:
        style["text_color"] = color
    run: dict[str, Any] = {"content": content}
    if style:
        run["text_element_style"] = style
    return {"text_run": run}


def _paragraph(*runs: dict) -> dict:
    return {"block_type": _BT_TEXT, "text": {"elements": list(runs), "style": {}}}


def _heading(level: int, text: str, bold: bool = True) -> dict:
    bt = {1: _BT_H1, 2: _BT_H2, 3: _BT_H3}.get(level, _BT_H2)
    key = {_BT_H1: "heading1", _BT_H2: "heading2", _BT_H3: "heading3"}[bt]
    return {
        "block_type": bt,
        key: {"elements": [_text_run(text, bold=bold)], "style": {}},
    }


def _bullet(text: str, *, bold: bool = False) -> dict:
    return {
        "block_type": _BT_BULLET,
        "bullet": {"elements": [_text_run(text, bold=bold)], "style": {}},
    }


def _blank() -> dict:
    return _paragraph(_text_run(""))


# ─── Table spec (deferred table content) ─────────────────────────────


@dataclass
class TableSpec:
    """Represents a table to be created via the Feishu table API."""
    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)
    column_widths: Optional[list[int]] = None


# ─── Report section builders ─────────────────────────────────────────


def _severity_label(s: Severity | str) -> str:
    labels = {
        Severity.CRITICAL: "🔴 严重",
        Severity.HIGH: "🟠 高",
        Severity.MEDIUM: "🟡 中",
        Severity.LOW: "🟢 低",
        Severity.INFO: "⚪ 信息",
    }
    if isinstance(s, Severity):
        return labels.get(s, str(s))
    try:
        return labels.get(Severity(s), s)
    except ValueError:
        return str(s)


def _build_content(report: AnalysisReport) -> list[dict | TableSpec]:
    """Build the ordered content list (simple blocks + TableSpecs)."""
    items: list[dict | TableSpec] = []

    # ── Title / meta ───────────────────────────────────────────────────────────
    analyzed_at_local = report.analyzed_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    items.append(
        _paragraph(
            _text_run(f"📅 生成时间：{analyzed_at_local}　", bold=False),
            _text_run(f"📊 状态：{report.overall_status}　"),
            _text_run(f"❌ 失败阶段：{report.failure_stage_name or '—'}"),
        )
    )
    items.append(_blank())

    # ── Module versions ────────────────────────────────────────────────────────
    items.append(_heading(1, "📦 模块版本信息"))
    if report.module_versions:
        for mod, ver in report.module_versions.items():
            items.append(_bullet(f"{mod}：{ver}"))
    else:
        items.append(_paragraph(_text_run("暂无版本信息")))
    items.append(_blank())

    # ── Test results — counts only, one per line ───────────────────────────────
    items.append(_heading(1, "📋 测试用例统计"))
    items.append(_bullet(f"总计：{report.total_tests}", bold=True))
    items.append(_bullet(f"✅ 通过：{report.passed_tests}"))
    items.append(_bullet(f"❌ 失败：{report.failed_tests}"))
    items.append(_bullet(f"💥 异常：{report.broken_tests}"))
    items.append(_bullet(f"⏭️ 跳过：{report.skipped_tests}"))
    items.append(_blank())

    # ── Bug recommendations table ──────────────────────────────────────────────
    items.append(_heading(1, f"🐛 Bug 上报推荐（共 {len(report.bug_recommendations)} 条）"))
    if report.bug_recommendations:
        items.append(_build_bug_table(report.bug_recommendations))
    else:
        items.append(_paragraph(_text_run("无 Bug 上报推荐")))
    items.append(_blank())

    # ── Issues detail table ────────────────────────────────────────────────────
    items.append(_heading(1, f"🔍 问题分析详情（共 {len(report.issues)} 个）"))
    if report.issues:
        items.append(_build_issue_table(report.issues))
    else:
        items.append(_paragraph(_text_run("未发现明显问题")))

    return items


def _build_bug_table(recs: list[BugRecommendation]) -> TableSpec:
    headers = ["#", "摘要", "严重度", "阶段", "详细描述", "关联任务"]
    rows = []
    for i, rec in enumerate(recs, 1):
        sev = _severity_label(rec.severity) if rec.severity else "—"
        stage = rec.affected_stage_name or "—"
        jobs = "、".join(rec.affected_jobs[:5]) if rec.affected_jobs else "—"
        rows.append([str(i), rec.summary, sev, stage, rec.detail_description, jobs])
    return TableSpec(headers=headers, rows=rows, column_widths=[40, 160, 80, 100, 240, 140])


def _build_issue_table(issues: list[AnalyzedIssue]) -> TableSpec:
    headers = ["#", "标题", "严重度", "分类", "阶段", "描述", "根因", "建议"]
    rows = []
    for i, issue in enumerate(issues, 1):
        sev = _severity_label(issue.severity)
        label = getattr(issue, "category_label", str(issue.category))
        stage = issue.stage_name or "—"
        root_cause = issue.root_cause or "—"
        suggestion = issue.suggestion or "—"
        rows.append([str(i), issue.title, sev, label, stage, issue.description, root_cause, suggestion])
    return TableSpec(headers=headers, rows=rows, column_widths=[40, 140, 80, 100, 100, 200, 160, 160])


# ─── Document API calls ──────────────────────────────────────────────


async def _get_document_url(client: httpx.AsyncClient, token: str, document_id: str, folder_token: str = "") -> str:
    """Fetch the actual tenant-specific URL for a document via drive batch meta API."""
    resp = await client.post(
        f"{_FEISHU_BASE}/drive/v1/metas/batch_query",
        headers={"Authorization": f"Bearer {token}"},
        json={"request_docs": [{"doc_token": document_id, "doc_type": "docx"}], "with_url": True},
    )
    if resp.is_success:
        metas = resp.json().get("data", {}).get("metas", [])
        if metas:
            url = metas[0].get("url", "")
            if url:
                return url
    return f"https://docs.feishu.cn/docx/{document_id}"


async def _create_document(
    client: httpx.AsyncClient, token: str, title: str, folder_token: str = ""
) -> tuple[str, str]:
    """Create an empty Feishu docx. Returns (document_id, document_url)."""
    body: dict[str, Any] = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    resp = await client.post(
        f"{_FEISHU_BASE}/docx/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Feishu create document HTTP {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu create document failed: {data}")
    doc = data["data"]["document"]
    document_id = doc["document_id"]
    doc_url = await _get_document_url(client, token, document_id, folder_token=folder_token)
    return document_id, doc_url


async def _append_blocks(
    client: httpx.AsyncClient, token: str, document_id: str, blocks: list[dict], batch_size: int = 50
) -> None:
    """Append simple blocks to the document root in batches."""
    import asyncio
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i : i + batch_size]
        for attempt in range(3):
            resp = await client.post(
                f"{_FEISHU_BASE}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                headers={"Authorization": f"Bearer {token}"},
                json={"children": batch, "index": -1},
            )
            if resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            if not resp.is_success:
                raise RuntimeError(
                    f"Feishu append blocks HTTP {resp.status_code} (batch {i}): {resp.text}"
                )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu append blocks failed (batch {i}): {data}")
            break
        else:
            raise RuntimeError(f"Feishu append blocks rate limited after retries (batch {i})")


async def _create_table(
    client: httpx.AsyncClient, token: str, document_id: str, table: TableSpec
) -> None:
    """Create a table block and fill all cells with content."""
    n_rows = len(table.rows) + 1  # header row + data rows
    n_cols = len(table.headers)

    table_prop: dict[str, Any] = {"row_size": n_rows, "column_size": n_cols}
    if table.column_widths and len(table.column_widths) == n_cols:
        table_prop["column_width"] = table.column_widths

    resp = await client.post(
        f"{_FEISHU_BASE}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
        headers={"Authorization": f"Bearer {token}"},
        json={"children": [{"block_type": _BT_TABLE, "table": {"property": table_prop}}], "index": -1},
    )
    if not resp.is_success:
        raise RuntimeError(f"Feishu create table HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu create table failed: {data}")

    cells: list[str] = data["data"]["children"][0]["table"]["cells"]

    import asyncio
    all_rows = [table.headers] + table.rows
    for row_idx, row in enumerate(all_rows):
        is_header = row_idx == 0
        for col_idx, text in enumerate(row):
            cell_id = cells[row_idx * n_cols + col_idx]
            for attempt in range(3):
                resp2 = await client.post(
                    f"{_FEISHU_BASE}/docx/v1/documents/{document_id}/blocks/{cell_id}/children",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"children": [_paragraph(_text_run(text, bold=is_header))], "index": -1},
                )
                if resp2.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if not resp2.is_success or resp2.json().get("code") != 0:
                    logger.warning(
                        "feishu_reporter: failed to write cell [%d][%d]: %s", row_idx, col_idx, resp2.text[:200]
                    )
                break


async def _write_content(
    client: httpx.AsyncClient, token: str, document_id: str, items: list[dict | TableSpec]
) -> None:
    """Write mixed content (blocks + tables) to the document in order."""
    pending: list[dict] = []

    for item in items:
        if isinstance(item, TableSpec):
            if pending:
                await _append_blocks(client, token, document_id, pending)
                pending = []
            await _create_table(client, token, document_id, item)
        else:
            pending.append(item)

    if pending:
        await _append_blocks(client, token, document_id, pending)


# ─── Public API ──────────────────────────────────────────────────────


async def create_feishu_report(
    report: AnalysisReport,
    app_id: str,
    app_secret: str,
    folder_token: str = "",
) -> str:
    """Create a Feishu cloud document from the analysis report.

    Returns the public URL of the created document.
    """
    title = f"{report.job_name} #{report.build_number} 测试分析报告"
    items = _build_content(report)

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_tenant_access_token(client, app_id, app_secret)
        document_id, doc_url = await _create_document(client, token, title, folder_token=folder_token)
        n_items = len(items)
        logger.info("feishu_reporter: created document %s (%d content items)", document_id, n_items)
        await _write_content(client, token, document_id, items)

    logger.info("feishu_reporter: document ready at %s", doc_url)
    return doc_url


async def list_feishu_reports(
    app_id: str,
    app_secret: str,
    folder_token: str = "",
) -> list[dict]:
    """List all Feishu documents in the configured folder.

    Returns a list of dicts with keys: name, url, created_time, modified_time.
    """
    import datetime

    async with httpx.AsyncClient(timeout=15.0) as client:
        token = await _get_tenant_access_token(client, app_id, app_secret)

        # Use explorer API to list folder children (works for bot-owned folders)
        if folder_token:
            resp = await client.get(
                f"{_FEISHU_BASE}/drive/explorer/v2/folder/{folder_token}/children",
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            resp = await client.get(
                f"{_FEISHU_BASE}/drive/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                params={"parent_type": "my_space", "page_size": 50},
            )

        if not resp.is_success:
            raise RuntimeError(f"Failed to list files: {resp.status_code}")

        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu list files failed: {data}")

        result = []
        if folder_token:
            # Explorer API returns children dict
            children = data.get("data", {}).get("children", {})
            tokens = [v.get("token") for v in children.values() if v.get("type") == "docx"]
            # Batch get metadata for URLs and timestamps
            if tokens:
                meta_resp = await client.post(
                    f"{_FEISHU_BASE}/drive/v1/metas/batch_query",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "request_docs": [{"doc_token": t, "doc_type": "docx"} for t in tokens],
                        "with_url": True,
                    },
                )
                if meta_resp.is_success and meta_resp.json().get("code") == 0:
                    for meta in meta_resp.json().get("data", {}).get("metas", []):
                        created_ts = int(meta.get("create_time", 0))
                        result.append({
                            "name": meta.get("title", ""),
                            "url": meta.get("url", ""),
                            "created_time": datetime.datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S") if created_ts else "",
                        })
        else:
            for f in data.get("data", {}).get("files", []):
                if f.get("type") == "docx":
                    created_ts = int(f.get("created_time", 0))
                    result.append({
                        "name": f.get("name", ""),
                        "url": f.get("url", ""),
                        "created_time": datetime.datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S") if created_ts else "",
                    })

        # Sort by created_time descending (newest first)
        result.sort(key=lambda x: x.get("created_time", ""), reverse=True)
        return result
