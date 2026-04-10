"""Tests for the skill system."""

import tempfile
from pathlib import Path

from test_analysis_agent.skills.base import BaseSkill, SkillRegistry
from test_analysis_agent.skills.log_pattern_skill import LogPatternAnalyzerSkill


class DummySkill(BaseSkill):
    """A test skill for unit testing."""

    name = "dummy_skill"
    description = "A dummy skill for testing"
    version = "0.0.1"

    def can_handle(self, context):
        return "test_data" in context

    def execute(self, context):
        return {"result": "dummy_executed", "input": context.get("test_data")}


class TestSkillRegistry:
    """Tests for the skill registry."""

    def test_register_and_list(self):
        registry = SkillRegistry()
        skill = DummySkill()
        registry.register(skill)

        skills = registry.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "dummy_skill"

    def test_get_skill(self):
        registry = SkillRegistry()
        skill = DummySkill()
        registry.register(skill)

        retrieved = registry.get("dummy_skill")
        assert retrieved is skill

    def test_get_missing_skill(self):
        registry = SkillRegistry()
        assert registry.get("nonexistent") is None

    def test_unregister(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        registry.unregister("dummy_skill")
        assert registry.get("dummy_skill") is None

    def test_find_applicable(self):
        registry = SkillRegistry()
        registry.register(DummySkill())

        applicable = registry.find_applicable({"test_data": "hello"})
        assert len(applicable) == 1

        applicable = registry.find_applicable({"other_data": "hello"})
        assert len(applicable) == 0

    def test_execute_applicable(self):
        registry = SkillRegistry()
        registry.register(DummySkill())

        results = registry.execute_applicable({"test_data": "hello"})
        assert len(results) == 1
        assert results[0]["result"] == "dummy_executed"
        assert results[0]["_skill_name"] == "dummy_skill"

    def test_load_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a skill file
            skill_file = Path(tmpdir) / "custom_skill.py"
            skill_file.write_text("""
from test_analysis_agent.skills.base import BaseSkill

class CustomSkill(BaseSkill):
    name = "custom_from_dir"
    description = "Loaded from directory"
    version = "1.0.0"

    def can_handle(self, context):
        return True

    def execute(self, context):
        return {"loaded": True}
""")

            registry = SkillRegistry()
            loaded = registry.load_from_directory(tmpdir)
            assert loaded == 1
            assert registry.get("custom_from_dir") is not None

    def test_load_from_nonexistent_directory(self):
        registry = SkillRegistry()
        loaded = registry.load_from_directory("/nonexistent/path")
        assert loaded == 0


class TestLogPatternSkill:
    """Tests for the built-in log pattern analyzer skill."""

    def test_can_handle(self):
        skill = LogPatternAnalyzerSkill()
        assert skill.can_handle({"log_text": "some log"}) is True
        assert skill.can_handle({}) is False
        assert skill.can_handle({"log_text": ""}) is False

    def test_detect_network_error(self):
        skill = LogPatternAnalyzerSkill()
        result = skill.execute({"log_text": "Connection refused to server\nRetrying..."})
        assert result["has_known_patterns"] is True
        findings = result["pattern_findings"]
        assert any(f["category"] == "network_error" for f in findings)

    def test_detect_permission_error(self):
        skill = LogPatternAnalyzerSkill()
        result = skill.execute({"log_text": "Permission denied: /var/data"})
        assert result["has_known_patterns"] is True
        findings = result["pattern_findings"]
        assert any(f["category"] == "permission_error" for f in findings)

    def test_detect_resource_error(self):
        skill = LogPatternAnalyzerSkill()
        result = skill.execute({"log_text": "No space left on device\nOut of memory"})
        findings = result["pattern_findings"]
        assert any(f["category"] == "resource_error" for f in findings)

    def test_detect_dependency_error(self):
        skill = LogPatternAnalyzerSkill()
        result = skill.execute({"log_text": "ModuleNotFoundError: No module named 'flask'"})
        findings = result["pattern_findings"]
        assert any(f["category"] == "dependency_error" for f in findings)

    def test_detect_deployment_error(self):
        skill = LogPatternAnalyzerSkill()
        result = skill.execute({"log_text": "Pod status: ImagePullBackOff"})
        findings = result["pattern_findings"]
        assert any(f["category"] == "deployment_error" for f in findings)

    def test_no_patterns(self):
        skill = LogPatternAnalyzerSkill()
        result = skill.execute({"log_text": "INFO: Everything is fine\nBuild succeeded"})
        assert result["has_known_patterns"] is False
        assert len(result["pattern_findings"]) == 0
