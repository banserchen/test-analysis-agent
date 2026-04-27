"""Allure report parser.

Parses Allure report JSON files to extract test results,
failure details, and categorization.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from test_analysis_agent.models.schemas import TestCaseFailure

logger = logging.getLogger(__name__)


class AllureReportParser:
    """Parses Allure report data from local files or HTTP endpoint."""

    def __init__(
        self,
        report_path: Optional[str] = None,
        report_url: Optional[str] = None,
    ):
        self._report_path = Path(report_path) if report_path else None
        self._report_url = report_url.rstrip("/") if report_url else None

    def parse(self) -> AllureReportData:
        """Parse the Allure report and return structured data."""
        if self._report_path:
            return self._parse_from_path(self._report_path)
        if self._report_url:
            return self._parse_from_url(self._report_url)
        raise ValueError("Either report_path or report_url must be provided")

    def _parse_from_path(self, base_path: Path) -> AllureReportData:
        """Parse Allure report from local filesystem."""
        # Allure generates data in multiple JSON files
        # Key files: suites.json, categories.json, behaviors.json,
        # plus individual test case files in data/test-cases/
        data_dir = base_path / "data"
        widgets_dir = base_path / "widgets"

        summary = self._load_json_file(widgets_dir / "summary.json")
        suites = self._load_json_file(data_dir / "suites.json")
        categories = self._load_json_file(widgets_dir / "categories.json")

        # Parse individual test case results
        test_cases_dir = data_dir / "test-cases"
        test_case_details: list[dict[str, Any]] = []
        if test_cases_dir.exists():
            for tc_file in sorted(test_cases_dir.glob("*.json")):
                tc_data = self._load_json_file(tc_file)
                if tc_data:
                    test_case_details.append(tc_data)

        return AllureReportData(
            summary=summary or {},
            suites=suites or {},
            categories=categories or [],
            test_case_details=test_case_details,
        )

    def _parse_from_url(self, base_url: str) -> AllureReportData:
        """Parse Allure report from HTTP endpoint."""
        summary = self._fetch_json(f"{base_url}/widgets/summary.json")
        suites = self._fetch_json(f"{base_url}/data/suites.json")
        categories = self._fetch_json(f"{base_url}/widgets/categories.json")

        # Enumerate failing test UIDs from the suites tree and fetch their details.
        test_case_details: list[dict] = []
        if suites:
            failed_uids: list[str] = []
            self._collect_failed_uids(suites.get("children", []), failed_uids)
            for uid in failed_uids:
                detail = self._fetch_json(f"{base_url}/data/test-cases/{uid}.json")
                if detail:
                    test_case_details.append(detail)

        return AllureReportData(
            summary=summary or {},
            suites=suites or {},
            categories=categories or [],
            test_case_details=test_case_details,
        )

    @staticmethod
    def _collect_failed_uids(nodes: list[dict], uids: list[str]) -> None:
        """Recursively collect UIDs of failed/broken test nodes from a suites tree."""
        for node in nodes:
            status = node.get("status", "").lower()
            uid = node.get("uid")
            if status in ("failed", "broken") and uid:
                uids.append(uid)
            children = node.get("children", [])
            if children:
                AllureReportParser._collect_failed_uids(children, uids)

    @staticmethod
    def _load_json_file(path: Path) -> Any:
        """Load and parse a JSON file."""
        if not path.exists():
            logger.debug("File not found: %s", path)
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            return None

    @staticmethod
    def _fetch_json(url: str) -> Any:
        """Fetch and parse JSON from a URL."""
        try:
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("Failed to fetch %s: %s", url, exc)
            return None


class AllureReportData:
    """Structured Allure report data with methods to extract failures."""

    def __init__(
        self,
        summary: dict[str, Any],
        suites: dict[str, Any],
        categories: list[dict[str, Any]],
        test_case_details: list[dict[str, Any]],
    ):
        self.summary = summary
        self.suites = suites
        self.categories = categories
        self.test_case_details = test_case_details

    @property
    def statistics(self) -> dict[str, int]:
        """Get test statistics from summary."""
        statistic = self.summary.get("statistic", {})
        return {
            "total": statistic.get("total", 0),
            "passed": statistic.get("passed", 0),
            "failed": statistic.get("failed", 0),
            "broken": statistic.get("broken", 0),
            "skipped": statistic.get("skipped", 0),
            "unknown": statistic.get("unknown", 0),
        }

    def get_failures(self) -> list[TestCaseFailure]:
        """Extract all failed and broken test cases."""
        failures: list[TestCaseFailure] = []

        # First try detailed test case files
        if self.test_case_details:
            for tc in self.test_case_details:
                status = tc.get("status", "").lower()
                if status in ("failed", "broken"):
                    failures.append(_detail_to_failure(tc))
            return failures

        # Fallback: extract from suites tree
        children = self.suites.get("children", [])
        self._walk_suite_tree(children, failures)
        return failures

    def _walk_suite_tree(
        self,
        nodes: list[dict[str, Any]],
        failures: list[TestCaseFailure],
        suite_name: str = "",
    ) -> None:
        """Recursively walk the suites tree to find failures."""
        for node in nodes:
            name = node.get("name", "")
            current_suite = f"{suite_name}.{name}" if suite_name else name

            # Leaf node (test case)
            status = node.get("status", "").lower()
            if status in ("failed", "broken"):
                failures.append(
                    TestCaseFailure(
                        test_name=node.get("name", "unknown"),
                        test_class=node.get("parentName"),
                        suite_name=current_suite,
                        status=status,
                        error_message=_get_status_message(node),
                        stack_trace=_get_status_trace(node),
                        duration_ms=node.get("time", {}).get("duration"),
                    )
                )

            # Recurse into children
            children = node.get("children", [])
            if children:
                self._walk_suite_tree(children, failures, current_suite)

    def has_test_results(self) -> bool:
        """Check if the report contains any test results."""
        stats = self.statistics
        return stats.get("total", 0) > 0

    def get_category_defects(self) -> list[dict[str, Any]]:
        """Get categorized defects from the report."""
        return self.categories if isinstance(self.categories, list) else []


def _detail_to_failure(tc: dict[str, Any]) -> TestCaseFailure:
    """Convert an Allure test case detail to a TestCaseFailure.

    Handles two field layouts:
    - Local allure-results: ``statusDetails: {message: ..., trace: ...}``
    - Jenkins allure plugin API: top-level ``statusMessage`` and ``statusTrace``
    """
    labels = tc.get("labels", []) or []

    suite_name = ""
    test_class = ""
    for label in labels:
        if label.get("name") == "suite":
            suite_name = label.get("value", "")
        if label.get("name") == "testClass":
            test_class = label.get("value", "")

    categories = []
    for label in labels:
        if label.get("name") == "tag":
            categories.append(label.get("value", ""))

    # Support both statusDetails dict and flat statusMessage/statusTrace fields.
    status_details = tc.get("statusDetails") or {}
    if isinstance(status_details, dict):
        error_message = status_details.get("message") or tc.get("statusMessage")
        stack_trace = status_details.get("trace") or tc.get("statusTrace")
    else:
        error_message = tc.get("statusMessage")
        stack_trace = tc.get("statusTrace")

    # Also try testStage for nested message/trace (Jenkins allure plugin layout).
    if not error_message or not stack_trace:
        test_stage = tc.get("testStage") or {}
        error_message = error_message or test_stage.get("statusMessage")
        stack_trace = stack_trace or test_stage.get("statusTrace")

    return TestCaseFailure(
        test_name=tc.get("fullName") or tc.get("name", "unknown"),
        test_class=test_class or None,
        suite_name=suite_name or None,
        status=tc.get("status", "unknown").lower(),
        error_message=error_message,
        stack_trace=stack_trace,
        duration_ms=tc.get("time", {}).get("duration"),
        categories=categories,
    )


def _get_status_message(node: dict[str, Any]) -> Optional[str]:
    """Extract status message from a suite tree node."""
    details = node.get("statusDetails") or node.get("statusMessage")
    if isinstance(details, dict):
        return details.get("message")
    if isinstance(details, str):
        return details
    return None


def _get_status_trace(node: dict[str, Any]) -> Optional[str]:
    """Extract status trace from a suite tree node."""
    details = node.get("statusDetails")
    if isinstance(details, dict):
        return details.get("trace")
    # Jenkins allure plugin uses top-level statusTrace
    trace = node.get("statusTrace")
    if isinstance(trace, str):
        return trace
    return None
