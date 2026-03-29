"""Unit tests for the Task Breakdown Agent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.task_breakdown import (
    ETLOperation,
    ETLSpec,
    TaskBreakdownAgent,
    UserStory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm():
    """Return a mock ClaudeClient that returns a valid ETLSpec."""
    llm = MagicMock()
    llm.complete_json.return_value = ETLSpec(
        story_id="STORY-001",
        title="Clean and filter customer data",
        summary="Remove null emails, filter active customers, deduplicate by customer_id.",
        source_datasets=[
            {"name": "customers", "format": "parquet", "path": "gs://raw/customers/"},
        ],
        target_dataset={"name": "tbl_customers_clean", "format": "delta", "path": "gs://processed/customers_clean/"},
        operations=[
            ETLOperation(type="filter", description="Keep only active customers", config={"column": "status", "value": "active"}),
            ETLOperation(type="filter", description="Remove null emails", config={"column": "email_address", "null_check": True}),
            ETLOperation(type="dedupe", description="Deduplicate by customer_id", config={"key": "customer_id"}),
        ],
        quality_requirements=["No null email_address", "No duplicate customer_id"],
        assumptions=["status column exists with values: active, inactive"],
    )
    return llm


@pytest.fixture()
def agent(mock_llm):
    with patch("builtins.open", patch_yaml_reads()):
        return TaskBreakdownAgent(
            llm=mock_llm,
            prompts_path="config/agent_prompts.yaml",
            framework_config_path="config/framework_config.yaml",
        )


def patch_yaml_reads():
    """Return a context manager that mocks open() for YAML config files."""
    import io
    from unittest.mock import mock_open

    yaml_content = """
task_breakdown:
  system: "You are a DE expert."
  user: "Parse: {story_json} with config: {framework_config}"
code_generator:
  system: ""
  user: ""
  refinement: ""
test_generator:
  system: ""
  user: ""
pr_description:
  system: ""
  user: ""
commit_message:
  system: ""
  user: ""
"""
    return mock_open(read_data=yaml_content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTaskBreakdownAgent:
    def test_parse_story_returns_etl_spec(self, mock_llm):
        """parse_story() should return a validated ETLSpec."""
        with patch("builtins.open", patch_yaml_reads()):
            agent = TaskBreakdownAgent(mock_llm)

        story = UserStory(
            id="STORY-001",
            title="Clean customers",
            description="Remove null emails and active filter",
        )
        result = agent.parse_story(story)

        assert isinstance(result, ETLSpec)
        assert result.story_id == "STORY-001"
        assert len(result.operations) == 3

    def test_parse_story_dict_accepts_dict(self, mock_llm):
        """parse_story_dict() should accept a raw dict."""
        with patch("builtins.open", patch_yaml_reads()):
            agent = TaskBreakdownAgent(mock_llm)

        story_dict = {
            "id": "STORY-001",
            "title": "Test",
            "description": "Do something",
        }
        result = agent.parse_story_dict(story_dict)
        assert isinstance(result, ETLSpec)

    def test_operations_have_expected_types(self, mock_llm):
        """All returned operations should have recognised types."""
        with patch("builtins.open", patch_yaml_reads()):
            agent = TaskBreakdownAgent(mock_llm)

        story = UserStory(id="S-1", title="t", description="d")
        spec = agent.parse_story(story)

        valid_types = {"filter", "join", "aggregate", "dedupe", "enrich", "upsert"}
        for op in spec.operations:
            assert op.type in valid_types, f"Unknown operation type: {op.type}"

    def test_llm_called_once_per_story(self, mock_llm):
        """LLM should be invoked exactly once per parse_story call."""
        with patch("builtins.open", patch_yaml_reads()):
            agent = TaskBreakdownAgent(mock_llm)

        story = UserStory(id="S-2", title="t", description="d")
        agent.parse_story(story)
        mock_llm.complete_json.assert_called_once()

    def test_spec_includes_source_and_target(self, mock_llm):
        """ETLSpec must have at least one source dataset and a target dataset."""
        with patch("builtins.open", patch_yaml_reads()):
            agent = TaskBreakdownAgent(mock_llm)

        story = UserStory(id="S-3", title="t", description="d")
        spec = agent.parse_story(story)

        assert len(spec.source_datasets) >= 1
        assert spec.target_dataset is not None
        assert spec.target_dataset.format == "delta"


class TestUserStory:
    def test_minimal_story(self):
        story = UserStory(id="S-1", title="title", description="desc")
        assert story.acceptance_criteria == []
        assert story.labels == []

    def test_full_story(self):
        story = UserStory(
            id="STORY-042",
            title="Full story",
            description="Complex pipeline",
            acceptance_criteria=["criterion 1"],
            labels=["label1"],
        )
        assert len(story.acceptance_criteria) == 1
        assert story.labels == ["label1"]


class TestETLSpec:
    def test_spec_validation(self):
        spec = ETLSpec(
            story_id="S-1",
            title="test",
            summary="test summary",
            source_datasets=[{"name": "src", "format": "parquet", "path": "gs://b/p"}],
            target_dataset={"name": "tgt", "format": "delta", "path": "gs://b/t"},
            operations=[
                ETLOperation(type="filter", description="filter desc"),
            ],
        )
        assert len(spec.operations) == 1
        assert spec.operations[0].type == "filter"
