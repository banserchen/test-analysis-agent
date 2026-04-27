"""Extensible skill/plugin system for the analysis agent.

Skills are modular capabilities that can be added to extend the agent's
analysis abilities. Each skill implements the BaseSkill interface.

For skills that need LLM capability, extend LLMBaseSkill instead of BaseSkill.
LLMBaseSkill automatically loads prompt.md from the skill's directory and
provides call_llm() / render_prompt() helpers.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from jinja2 import Template

from test_analysis_agent.models.schemas import AnalysisReport

if TYPE_CHECKING:
    pass

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


class LLMBaseSkill(BaseSkill):
    """Skill that uses LLM via its prompt.md template.

    Extend this class for any skill that needs to call an LLM.
    The prompt.md file in the skill's package directory is loaded as a
    Jinja2 template. Use render_prompt(**vars) to render it, then
    call_llm(prompt) to invoke the LLM with token tracking.

    The LLM analyzer is injected by SkillRegistry.set_llm_client() which is
    called by the agent after all skills are registered.

    Example:
        class MyLLMSkill(LLMBaseSkill):
            name = "my_llm_skill"
            description = "Analyzes X using LLM"

            def can_handle(self, context):
                return "x_data" in context

            def execute(self, context):
                prompt = self.render_prompt(data=context["x_data"])
                result_text = self.call_llm(prompt)
                return {"result": result_text}
    """

    def __init__(self) -> None:
        self._llm_analyzer: Optional[Any] = None  # LLMAnalyzer (injected)
        self._language: str = "Chinese"
        self._prompt_template: Optional[str] = None

    def set_llm_client(self, analyzer: Any, language: str = "Chinese") -> None:
        """Inject the LLM analyzer and language setting."""
        self._llm_analyzer = analyzer
        self._language = language
        self._load_prompt_template()

    def _load_prompt_template(self) -> None:
        """Load prompt.md from the skill's package directory."""
        try:
            module = sys.modules.get(type(self).__module__)
            if module and hasattr(module, "__file__") and module.__file__:
                prompt_path = Path(module.__file__).parent / "prompt.md"
                if prompt_path.exists():
                    self._prompt_template = prompt_path.read_text(encoding="utf-8")
                    logger.debug("Loaded prompt template for skill '%s'", self.name)
                else:
                    logger.debug("No prompt.md found for skill '%s' at %s", self.name, prompt_path)
        except Exception as exc:
            logger.warning("Failed to load prompt.md for skill '%s': %s", self.name, exc)

    def render_prompt(self, **kwargs: Any) -> str:
        """Render the prompt.md Jinja2 template with the given variables.

        The ``language`` variable is always injected automatically.
        """
        if not self._prompt_template:
            return ""
        template = Template(self._prompt_template)
        return template.render(language=self._language, **kwargs)

    def call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM with a rendered prompt, tracking token usage.

        Returns the LLM response text, or None if no LLM is available.
        """
        if not self._llm_analyzer:
            logger.warning("Skill '%s' called call_llm() but no LLM analyzer was injected", self.name)
            return None
        return self._llm_analyzer.call_llm(prompt)


class SkillRegistry:
    """Registry for managing and discovering skills."""

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def set_llm_client(self, analyzer: Any, language: str = "Chinese") -> None:
        """Inject LLM analyzer into all registered LLMBaseSkill instances.

        Call this after registering all skills so that LLM-capable skills
        can render their prompt.md templates and call the LLM.
        """
        for skill in self._skills.values():
            if isinstance(skill, LLMBaseSkill):
                skill.set_llm_client(analyzer, language)
                logger.debug("Injected LLM into skill '%s'", skill.name)

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

    def execute_skill(self, name: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute a specific skill by name, bypassing can_handle check.

        Returns the skill's result dict, or an empty dict if the skill is not
        registered or raises an exception.
        """
        skill = self._skills.get(name)
        if not skill:
            logger.warning("execute_skill: skill '%s' not found", name)
            return {}
        if not skill.can_handle(context):
            logger.debug("execute_skill: skill '%s' cannot handle given context", name)
            return {}
        try:
            logger.info("Executing skill (explicit): %s", name)
            return skill.execute(context)
        except Exception as exc:
            logger.error("Skill '%s' failed: %s", name, exc)
            return {}

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
