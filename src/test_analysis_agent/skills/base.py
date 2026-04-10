"""Extensible skill/plugin system for the analysis agent.

Skills are modular capabilities that can be added to extend the agent's
analysis abilities. Each skill implements the BaseSkill interface.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from test_analysis_agent.models.schemas import AnalysisReport

logger = logging.getLogger(__name__)


class BaseSkill(ABC):
    """Base class for all agent skills.

    To create a custom skill:
    1. Create a Python file in the skills directory
    2. Define a class that inherits from BaseSkill
    3. Implement the required methods
    4. The skill will be auto-discovered and loaded

    Example:
        class MyCustomSkill(BaseSkill):
            name = "my_custom_skill"
            description = "Analyzes custom log formats"

            def can_handle(self, context: dict) -> bool:
                return "custom_log" in context

            def execute(self, context: dict) -> dict:
                # Perform analysis
                return {"findings": [...]}
    """

    name: str = "base_skill"
    description: str = "Base skill"
    version: str = "1.0.0"

    @abstractmethod
    def can_handle(self, context: dict[str, Any]) -> bool:
        """Check if this skill can handle the given context.

        Args:
            context: Dictionary containing analysis context (logs, report data, etc.)

        Returns:
            True if this skill should be applied.
        """

    @abstractmethod
    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute the skill's analysis.

        Args:
            context: Dictionary containing analysis context.

        Returns:
            Dictionary with analysis results.
        """

    def post_process(self, report: AnalysisReport) -> AnalysisReport:
        """Optional post-processing hook applied to the final report.

        Override this to modify the analysis report after all skills have run.
        """
        return report


class SkillRegistry:
    """Registry for managing and discovering skills."""

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """Register a skill instance."""
        if skill.name in self._skills:
            logger.warning("Skill '%s' already registered, overwriting", skill.name)
        self._skills[skill.name] = skill
        logger.info("Registered skill: %s (v%s)", skill.name, skill.version)

    def unregister(self, name: str) -> None:
        """Unregister a skill by name."""
        self._skills.pop(name, None)

    def get(self, name: str) -> Optional[BaseSkill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_skills(self) -> list[dict[str, str]]:
        """List all registered skills."""
        return [
            {"name": s.name, "description": s.description, "version": s.version}
            for s in self._skills.values()
        ]

    def find_applicable(self, context: dict[str, Any]) -> list[BaseSkill]:
        """Find all skills that can handle the given context."""
        return [skill for skill in self._skills.values() if skill.can_handle(context)]

    def execute_applicable(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute all applicable skills and return their results."""
        results: list[dict[str, Any]] = []
        for skill in self.find_applicable(context):
            try:
                logger.info("Executing skill: %s", skill.name)
                result = skill.execute(context)
                result["_skill_name"] = skill.name
                results.append(result)
            except Exception as exc:
                logger.error("Skill '%s' failed: %s", skill.name, exc)
                results.append({"_skill_name": skill.name, "_error": str(exc)})
        return results

    def apply_post_processing(self, report: AnalysisReport) -> AnalysisReport:
        """Apply all skills' post-processing to the report."""
        for skill in self._skills.values():
            try:
                report = skill.post_process(report)
            except Exception as exc:
                logger.error("Post-processing by skill '%s' failed: %s", skill.name, exc)
        return report

    def load_from_directory(self, directory: str | Path) -> int:
        """Auto-discover and load skills from a directory.

        Each .py file in the directory is imported and scanned for
        BaseSkill subclasses. Instances are automatically registered.

        Returns:
            Number of skills loaded.
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            logger.warning("Skills directory not found: %s", dir_path)
            return 0

        loaded = 0
        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"skill_{py_file.stem}", py_file
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, BaseSkill)
                            and attr is not BaseSkill
                        ):
                            instance = attr()
                            self.register(instance)
                            loaded += 1
            except Exception as exc:
                logger.error("Failed to load skill from %s: %s", py_file, exc)

        logger.info("Loaded %d skills from %s", loaded, dir_path)
        return loaded
