"""Unit tests for the PySpark Code Generation Agent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.code_generator import CodeGeneratorAgent, GeneratedCode
from src.agents.task_breakdown import DatasetRef, ETLOperation, ETLSpec
from src.llm.claude_client import LLMResponse


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


VALID_PYSPARK_SCRIPT = '''"""Auto-generated PySpark ETL pipeline."""
import argparse
import structlog
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

logger = structlog.get_logger(__name__)


def build_spark() -> SparkSession:
    return (
        SparkSession.builder
        .appName("pipeline_story_001")
        .config("spark.sql.adaptive.enabled", "true")
        .getOrCreate()
    )


def filter_active_customers(df):
    """Filter to active customers with non-null emails."""
    return df.filter(F.col("status") == "active").filter(F.col("email_address").isNotNull())


def dedupe_customers(df):
    """Remove duplicate records by customer_id."""
    return df.dropDuplicates(["customer_id"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-path", required=True)
    parser.add_argument("--output-path", required=True)
    args = parser.parse_args()

    spark = build_spark()
    logger.info("pipeline_start")

    df = spark.read.parquet(args.input_path)
    df = filter_active_customers(df)
    df = dedupe_customers(df)

    df.write.format("delta").mode("overwrite").save(args.output_path)
    logger.info("pipeline_complete", output=args.output_path)


if __name__ == "__main__":
    main()
'''


@pytest.fixture()
def sample_spec() -> ETLSpec:
    return ETLSpec(
        story_id="STORY-001",
        title="Clean customers",
        summary="Filter and dedupe customer data.",
        source_datasets=[DatasetRef(name="customers", format="parquet", path="gs://raw/customers/")],
        target_dataset=DatasetRef(name="tbl_customers_clean", format="delta", path="gs://processed/customers_clean/"),
        operations=[
            ETLOperation(type="filter", description="Keep active customers"),
            ETLOperation(type="dedupe", description="Deduplicate by customer_id"),
        ],
    )


@pytest.fixture()
def mock_llm_good_code():
    llm = MagicMock()
    llm.complete.return_value = LLMResponse(
        content=VALID_PYSPARK_SCRIPT,
        input_tokens=500,
        output_tokens=300,
        model="claude-3-5-sonnet-20241022",
        latency_ms=1200.0,
    )
    return llm


def _make_agent(llm) -> CodeGeneratorAgent:
    with patch("builtins.open", _yaml_mock()):
        return CodeGeneratorAgent(llm)


def _yaml_mock():
    from unittest.mock import mock_open
    yaml_content = """
code_generator:
  system: "You are a PySpark expert."
  user: "Generate code for: {etl_spec_json} with {framework_config}"
  refinement: "Fix these issues in: {original_code}\\nIssues: {issues}"
quality_rules:
  min_test_coverage: 0.8
spark:
  write_mode: delta
"""
    return mock_open(read_data=yaml_content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCodeGeneratorAgent:
    def test_generate_returns_generated_code(self, sample_spec, mock_llm_good_code):
        agent = _make_agent(mock_llm_good_code)
        result = agent.generate(sample_spec)

        assert isinstance(result, GeneratedCode)
        assert len(result.script) > 0
        assert len(result.notebook) > 0

    def test_filename_uses_story_id(self, sample_spec, mock_llm_good_code):
        agent = _make_agent(mock_llm_good_code)
        result = agent.generate(sample_spec)

        assert "story_001" in result.filename
        assert result.filename.endswith(".py")

    def test_notebook_filename_uses_story_id(self, sample_spec, mock_llm_good_code):
        agent = _make_agent(mock_llm_good_code)
        result = agent.generate(sample_spec)

        assert "story_001" in result.notebook_filename
        assert result.notebook_filename.endswith(".ipynb")

    def test_notebook_is_valid_json(self, sample_spec, mock_llm_good_code):
        import json
        agent = _make_agent(mock_llm_good_code)
        result = agent.generate(sample_spec)

        notebook = json.loads(result.notebook)
        assert "cells" in notebook
        assert "nbformat" in notebook

    def test_validation_passes_for_valid_code(self, sample_spec, mock_llm_good_code):
        """The code validator should find no issues in the valid test script."""
        agent = _make_agent(mock_llm_good_code)
        issues = agent._validate_code(VALID_PYSPARK_SCRIPT, sample_spec)
        assert issues == [], f"Unexpected issues: {issues}"

    def test_validation_catches_missing_spark_session(self, sample_spec, mock_llm_good_code):
        bad_code = "def main():\n    pass\nif __name__ == '__main__':\n    main()\n"
        agent = _make_agent(mock_llm_good_code)
        issues = agent._validate_code(bad_code, sample_spec)
        assert any("SparkSession" in i for i in issues)

    def test_validation_catches_syntax_error(self, sample_spec, mock_llm_good_code):
        bad_code = "def broken(:\n    pass"
        agent = _make_agent(mock_llm_good_code)
        issues = agent._validate_code(bad_code, sample_spec)
        assert any("SyntaxError" in i for i in issues)

    def test_strips_markdown_fences(self, sample_spec):
        llm = MagicMock()
        llm.complete.return_value = LLMResponse(
            content=f"```python\n{VALID_PYSPARK_SCRIPT}\n```",
            input_tokens=100,
            output_tokens=100,
            model="test",
            latency_ms=100.0,
        )
        agent = _make_agent(llm)
        result = agent.generate(sample_spec)
        assert not result.script.startswith("```")

    def test_refinement_called_on_bad_code(self, sample_spec):
        """Agent should call complete() twice when initial code fails validation."""
        llm = MagicMock()
        # First call returns bad code, second returns good code
        llm.complete.side_effect = [
            LLMResponse(content="def bad(): pass", input_tokens=100, output_tokens=50, model="test", latency_ms=100.0),
            LLMResponse(content=VALID_PYSPARK_SCRIPT, input_tokens=100, output_tokens=300, model="test", latency_ms=100.0),
        ]
        agent = _make_agent(llm)
        agent.generate(sample_spec)
        assert llm.complete.call_count >= 2
