"""Tests for Allure report parser."""

import json
import tempfile
from pathlib import Path

from test_analysis_agent.parsers.allure_parser import AllureReportData, AllureReportParser


class TestAllureReportData:
    """Tests for AllureReportData."""

    def test_statistics_from_summary(self):
        data = AllureReportData(
            summary={
                "statistic": {
                    "total": 100,
                    "passed": 90,
                    "failed": 5,
                    "broken": 3,
                    "skipped": 2,
                    "unknown": 0,
                }
            },
            suites={},
            categories=[],
            test_case_details=[],
        )
        stats = data.statistics
        assert stats["total"] == 100
        assert stats["passed"] == 90
        assert stats["failed"] == 5

    def test_empty_statistics(self):
        data = AllureReportData(summary={}, suites={}, categories=[], test_case_details=[])
        stats = data.statistics
        assert stats["total"] == 0

    def test_has_test_results(self):
        data = AllureReportData(
            summary={"statistic": {"total": 10}},
            suites={},
            categories=[],
            test_case_details=[],
        )
        assert data.has_test_results() is True

    def test_no_test_results(self):
        data = AllureReportData(
            summary={"statistic": {"total": 0}},
            suites={},
            categories=[],
            test_case_details=[],
        )
        assert data.has_test_results() is False

    def test_get_failures_from_details(self):
        data = AllureReportData(
            summary={},
            suites={},
            categories=[],
            test_case_details=[
                {
                    "name": "test_login",
                    "fullName": "tests.auth.test_login",
                    "status": "failed",
                    "statusDetails": {
                        "message": "AssertionError: expected 200",
                        "trace": "Traceback...",
                    },
                    "labels": [
                        {"name": "suite", "value": "auth"},
                        {"name": "testClass", "value": "TestAuth"},
                    ],
                    "time": {"duration": 1500},
                },
                {
                    "name": "test_logout",
                    "fullName": "tests.auth.test_logout",
                    "status": "passed",
                    "labels": [],
                },
            ],
        )
        failures = data.get_failures()
        assert len(failures) == 1
        assert failures[0].test_name == "tests.auth.test_login"
        assert failures[0].status == "failed"
        assert failures[0].error_message == "AssertionError: expected 200"
        assert failures[0].suite_name == "auth"

    def test_get_failures_from_suites_tree(self):
        data = AllureReportData(
            summary={},
            suites={
                "children": [
                    {
                        "name": "auth_suite",
                        "children": [
                            {
                                "name": "test_login_success",
                                "status": "passed",
                            },
                            {
                                "name": "test_login_fail",
                                "status": "failed",
                                "statusDetails": {"message": "Auth error"},
                            },
                        ],
                    }
                ]
            },
            categories=[],
            test_case_details=[],  # No details, fall back to suites
        )
        failures = data.get_failures()
        assert len(failures) == 1
        assert failures[0].test_name == "test_login_fail"

    def test_category_defects(self):
        data = AllureReportData(
            summary={},
            suites={},
            categories=[
                {"name": "Product defects", "children": [{"name": "broken_feature"}]},
            ],
            test_case_details=[],
        )
        defects = data.get_category_defects()
        assert len(defects) == 1


class TestAllureReportParserLocal:
    """Tests for AllureReportParser with local files."""

    def test_parse_from_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create minimal Allure report structure
            base = Path(tmpdir)
            data_dir = base / "data"
            widgets_dir = base / "widgets"
            test_cases_dir = data_dir / "test-cases"
            data_dir.mkdir()
            widgets_dir.mkdir()
            test_cases_dir.mkdir()

            # Write summary
            (widgets_dir / "summary.json").write_text(json.dumps({
                "statistic": {"total": 2, "passed": 1, "failed": 1, "broken": 0, "skipped": 0}
            }))

            # Write suites
            (data_dir / "suites.json").write_text(json.dumps({"children": []}))

            # Write categories
            (widgets_dir / "categories.json").write_text(json.dumps([]))

            # Write a test case
            (test_cases_dir / "tc1.json").write_text(json.dumps({
                "name": "test_example",
                "fullName": "tests.test_example",
                "status": "failed",
                "statusDetails": {"message": "Test failed", "trace": "..."},
                "labels": [],
                "time": {"duration": 100},
            }))

            parser = AllureReportParser(report_path=str(base))
            report_data = parser.parse()

            assert report_data.statistics["total"] == 2
            failures = report_data.get_failures()
            assert len(failures) == 1
            assert failures[0].test_name == "tests.test_example"

    def test_missing_files_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "data").mkdir()
            (base / "widgets").mkdir()

            parser = AllureReportParser(report_path=str(base))
            report_data = parser.parse()

            assert report_data.statistics["total"] == 0
            assert report_data.get_failures() == []
