"""Task Breakdown Agent — parses DevOps user stories into structured ETL operation specs."""

from __future__ import annotations

import json
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

from src.llm.claude_client import ClaudeClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models for structured input / output
# ---------------------------------------------------------------------------


class DatasetRef(BaseModel):
    name: str
    format: str = "parquet"            # parquet | csv | delta | json
    path: str                          # GCS path or table name


class ETLOperation(BaseModel):
    type: str                          # filter | join | aggregate | dedupe | enrich | upsert
    description: str
    config: dict[str, Any] = Field(default_factory=dict)


class ETLSpec(BaseModel):
    story_id: str
    title: str
    summary: str
    source_datasets: list[DatasetRef]
    target_dataset: DatasetRef
    operations: list[ETLOperation]
    quality_requirements: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class UserStory(BaseModel):
    id: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Task Breakdown Agent
# ---------------------------------------------------------------------------


class TaskBreakdownAgent:
    """Parses a UserStory into a structured ETLSpec using Claude.

    The agent uses prompt templates from config/agent_prompts.yaml and applies
    framework rules from config/framework_config.yaml to ensure generated ETL
    specs comply with project standards.
    """

    def __init__(
        self,
        llm: ClaudeClient,
        prompts_path: str = "config/agent_prompts.yaml",
        framework_config_path: str = "config/framework_config.yaml",
    ) -> None:
        self._llm = llm
        self._prompts = self._load_yaml(prompts_path)
        self._framework_config = self._load_yaml(framework_config_path)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse_story(self, story: UserStory) -> ETLSpec:
        """Parse a user story and return a structured ETLSpec.

        Args:
            story: The DevOps user story to parse.

        Returns:
            A validated ETLSpec describing the ETL pipeline to generate.
        """
        logger.info("task_breakdown_start", story_id=story.id, title=story.title)

        system_prompt = self._prompts["task_breakdown"]["system"]
        user_prompt = self._prompts["task_breakdown"]["user"].format(
            story_json=json.dumps(story.model_dump(), indent=2),
            framework_config=yaml.dump(self._framework_config, default_flow_style=False),
        )

        spec: ETLSpec = self._llm.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_model=ETLSpec,
        )

        logger.info(
            "task_breakdown_complete",
            story_id=story.id,
            num_operations=len(spec.operations),
            operations=[op.type for op in spec.operations],
        )
        return spec

    def parse_story_dict(self, story_dict: dict) -> ETLSpec:
        """Convenience wrapper accepting a raw dict/JSON payload."""
        story = UserStory.model_validate(story_dict)
        return self.parse_story(story)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
