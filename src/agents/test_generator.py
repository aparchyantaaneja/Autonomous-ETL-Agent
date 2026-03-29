"""Test Generation Agent — auto-creates pytest suites from PySpark code and ETL specs."""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog
import yaml
from pydantic import BaseModel

from src.agents.task_breakdown import ETLSpec
from src.llm.claude_client import ClaudeClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class TestGenerationOutput(BaseModel):
    conftest_py: str
    test_file: str
    fixtures_json: str


@dataclass
class GeneratedTests:
    conftest: str              # conftest.py content
    test_file: str             # test_pipeline_<story_id>.py content
    fixtures: list[dict]       # sample records as list of dicts
    conftest_filename: str
    test_filename: str
    fixtures_filename: str


# ---------------------------------------------------------------------------
# Test Generation Agent
# ---------------------------------------------------------------------------


class TestGeneratorAgent:
    """Generates pytest test suites for PySpark ETL pipelines.

    Produces:
    - conftest.py with shared SparkSession and sample DataFrame fixtures
    - test_pipeline_<story_id>.py with unit tests for every transformation
    - fixtures/<story_id>_test_data.json with mock input records
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

    def generate(self, pyspark_code: str, spec: ETLSpec) -> GeneratedTests:
        """Generate a full pytest test suite for the given PySpark code.

        Args:
            pyspark_code: The generated PySpark script to test.
            spec: The ETL specification the script was generated from.

        Returns:
            A GeneratedTests instance with conftest, test file, and fixtures.
        """
        logger.info("test_gen_start", story_id=spec.story_id)

        min_coverage = int(
            self._framework_config["quality_rules"]["min_test_coverage"] * 100
        )
        system_prompt = self._prompts["test_generator"]["system"]
        user_prompt = self._prompts["test_generator"]["user"].format(
            pyspark_code=pyspark_code,
            etl_spec_json=json.dumps(spec.model_dump(), indent=2),
            framework_config=yaml.dump(self._framework_config, default_flow_style=False),
            min_coverage=min_coverage,
        )

        output: TestGenerationOutput = self._llm.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_model=TestGenerationOutput,
        )

        safe_id = spec.story_id.lower().replace("-", "_")
        result = GeneratedTests(
            conftest=output.conftest_py,
            test_file=output.test_file,
            fixtures=json.loads(output.fixtures_json),
            conftest_filename="conftest.py",
            test_filename=f"test_pipeline_{safe_id}.py",
            fixtures_filename=f"{safe_id}_test_data.json",
        )

        logger.info(
            "test_gen_complete",
            story_id=spec.story_id,
            test_file=result.test_filename,
        )
        return result

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
