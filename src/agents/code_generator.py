"""PySpark Code Generation Agent — converts ETL specs into production-ready code and notebooks."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any

import structlog
import yaml

from src.agents.task_breakdown import ETLSpec
from src.llm.claude_client import ClaudeClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


@dataclass
class GeneratedCode:
    script: str          # Full .py PySpark script
    notebook: str        # .ipynb JSON string
    filename: str        # e.g., pipeline_story_42.py
    notebook_filename: str


# ---------------------------------------------------------------------------
# Code Generation Agent
# ---------------------------------------------------------------------------


class CodeGeneratorAgent:
    """Generates production-ready PySpark code from a structured ETLSpec.

    Uses a two-step LangGraph-style flow:
      1. Generate initial code from the ETL spec
      2. Validate syntax / standards; refine if issues found
    """

    MAX_REFINEMENT_ATTEMPTS = 2

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

    def generate(self, spec: ETLSpec) -> GeneratedCode:
        """Generate PySpark script and Jupyter notebook from an ETLSpec.

        Args:
            spec: The validated ETL operation specification.

        Returns:
            A GeneratedCode instance containing the script and notebook.
        """
        logger.info("code_gen_start", story_id=spec.story_id)

        code = self._generate_script(spec)
        code = self._refine_if_needed(code, spec)

        notebook = self._wrap_in_notebook(code, spec)
        safe_id = spec.story_id.lower().replace("-", "_")
        filename = f"pipeline_{safe_id}.py"
        notebook_filename = f"notebook_{safe_id}.ipynb"

        logger.info("code_gen_complete", story_id=spec.story_id, filename=filename)
        return GeneratedCode(
            script=code,
            notebook=notebook,
            filename=filename,
            notebook_filename=notebook_filename,
        )

    # ------------------------------------------------------------------
    # Internal: generation
    # ------------------------------------------------------------------

    def _generate_script(self, spec: ETLSpec) -> str:
        system_prompt = self._prompts["code_generator"]["system"]
        user_prompt = self._prompts["code_generator"]["user"].format(
            etl_spec_json=json.dumps(spec.model_dump(), indent=2),
            framework_config=yaml.dump(self._framework_config, default_flow_style=False),
        )

        response = self._llm.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.05,
        )
        code = response.content.strip()

        # Strip markdown fences if present
        if code.startswith("```"):
            code = code.split("```", 2)[1]
            if code.startswith("python"):
                code = code[6:]
            code = code.rsplit("```", 1)[0].strip()

        return code

    def _refine_if_needed(self, code: str, spec: ETLSpec) -> str:
        issues = self._validate_code(code, spec)
        if not issues:
            return code

        logger.warning(
            "code_gen_issues_found",
            story_id=spec.story_id,
            issues=issues,
        )

        for attempt in range(1, self.MAX_REFINEMENT_ATTEMPTS + 1):
            logger.info("code_gen_refinement", attempt=attempt)

            refinement_prompt = self._prompts["code_generator"]["refinement"].format(
                original_code=code,
                issues="\n".join(f"- {i}" for i in issues),
            )
            response = self._llm.complete(
                system_prompt=self._prompts["code_generator"]["system"],
                user_prompt=refinement_prompt,
                temperature=0.0,
            )
            refined = response.content.strip()
            if refined.startswith("```"):
                refined = refined.split("```", 2)[1]
                if refined.startswith("python"):
                    refined = refined[6:]
                refined = refined.rsplit("```", 1)[0].strip()

            issues = self._validate_code(refined, spec)
            if not issues:
                logger.info("code_gen_refinement_success", attempt=attempt)
                return refined

        logger.warning("code_gen_refinement_failed_using_best_effort")
        return code  # Return best-effort code even if issues remain

    # ------------------------------------------------------------------
    # Internal: validation
    # ------------------------------------------------------------------

    def _validate_code(self, code: str, spec: ETLSpec) -> list[str]:
        """Run lightweight static checks on generated code. Returns list of issues."""
        issues: list[str] = []

        # Syntax check via compile()
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError as exc:
            issues.append(f"SyntaxError: {exc}")
            return issues  # No point checking further if code won't parse

        # Structural checks
        if "SparkSession" not in code:
            issues.append("Missing SparkSession builder")
        if 'if __name__ == "__main__"' not in code:
            issues.append('Missing if __name__ == "__main__" guard')
        if "import argparse" not in code and "sys.argv" not in code:
            issues.append("No CLI argument handling (argparse/sys.argv)")
        if "structlog" not in code and "logging" not in code:
            issues.append("No logging found — add structlog or logging")

        # Operations check — verify key operations appear in code
        op_keywords = {
            "filter": ["filter", "where"],
            "join": [".join("],
            "aggregate": ["groupBy", "agg(", "groupby"],
            "dedupe": ["dropDuplicates", "drop_duplicates"],
            "upsert": ["merge", "MERGE"],
        }
        for op in spec.operations:
            keywords = op_keywords.get(op.type, [])
            if keywords and not any(kw in code for kw in keywords):
                issues.append(
                    f"Operation '{op.type}' missing expected code pattern "
                    f"({', '.join(keywords)})"
                )

        return issues

    # ------------------------------------------------------------------
    # Internal: notebook generation
    # ------------------------------------------------------------------

    def _wrap_in_notebook(self, code: str, spec: ETLSpec) -> str:
        """Wrap the PySpark script in an educational Jupyter notebook."""
        cells = [
            self._markdown_cell(f"# {spec.title}\n\n{spec.summary}"),
            self._markdown_cell(
                "## ETL Operations\n\n"
                + "\n".join(
                    f"- **{op.type.title()}**: {op.description}"
                    for op in spec.operations
                )
            ),
            self._markdown_cell("## Setup\nInitialize Spark and import dependencies."),
            self._code_cell(self._extract_imports_and_spark(code)),
            self._markdown_cell(
                "## Pipeline Code\n\nProduction-ready PySpark transformations generated "
                "from user story `" + spec.story_id + "`."
            ),
            self._code_cell(code),
            self._markdown_cell(
                "## Data Quality\nRun the pipeline and validate outputs against quality rules."
            ),
        ]

        notebook = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3 (PySpark)",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {"name": "python", "version": "3.11.0"},
            },
            "cells": cells,
        }
        return json.dumps(notebook, indent=2)

    @staticmethod
    def _markdown_cell(source: str) -> dict[str, Any]:
        return {
            "cell_type": "markdown",
            "id": "auto",
            "metadata": {},
            "source": source,
        }

    @staticmethod
    def _code_cell(source: str) -> dict[str, Any]:
        return {
            "cell_type": "code",
            "id": "auto",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": source,
        }

    @staticmethod
    def _extract_imports_and_spark(code: str) -> str:
        """Extract import statements and SparkSession setup from generated code."""
        lines: list[str] = []
        for line in code.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                lines.append(line)
            elif "SparkSession" in line and "builder" in line.lower():
                lines.append(line)
        return "\n".join(lines) if lines else "# Auto-extracted imports"

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
