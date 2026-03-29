"""PR Automation Agent — creates feature branches, commits generated code, and opens GitHub PRs."""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog
import yaml
from github import Github, GithubException
from pydantic import BaseModel

from src.agents.code_generator import GeneratedCode
from src.agents.task_breakdown import ETLSpec
from src.agents.test_generator import GeneratedTests
from src.llm.claude_client import ClaudeClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class PRDescription(BaseModel):
    title: str
    body: str


@dataclass
class PRResult:
    pr_url: str
    pr_number: int
    branch_name: str
    commit_sha: str


# ---------------------------------------------------------------------------
# PR Automation Agent
# ---------------------------------------------------------------------------


class PRAgent:
    """Creates GitHub PRs containing all generated ETL artifacts.

    Workflow:
      1. Generate PR title + description via Claude
      2. Create a new feature branch from base branch
      3. Commit script, notebook, tests, and fixtures
      4. Open a pull request linked to the original user story
    """

    def __init__(
        self,
        github_token: str,
        repo_owner: str,
        repo_name: str,
        base_branch: str,
        llm: ClaudeClient,
        prompts_path: str = "config/agent_prompts.yaml",
    ) -> None:
        self._gh = Github(github_token)
        self._repo = self._gh.get_repo(f"{repo_owner}/{repo_name}")
        self._base_branch = base_branch
        self._llm = llm
        self._prompts = self._load_yaml(prompts_path)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def create_pr(
        self,
        spec: ETLSpec,
        code: GeneratedCode,
        tests: GeneratedTests,
        test_results: dict | None = None,
    ) -> PRResult:
        """Create a pull request with all generated ETL artifacts.

        Args:
            spec: The ETL specification driving this pipeline.
            code: Generated PySpark script and notebook.
            tests: Generated pytest suite and fixtures.
            test_results: Optional dict with test pass/fail summary.

        Returns:
            A PRResult with the PR URL, number, branch name, and commit SHA.
        """
        logger.info("pr_agent_start", story_id=spec.story_id)

        branch_name = self._make_branch_name(spec.story_id)
        pr_desc = self._generate_pr_description(spec, code, tests, test_results)
        commit_message = self._generate_commit_message(spec)

        # Create branch
        self._create_branch(branch_name)

        # Commit all artifacts in one tree
        commit_sha = self._commit_artifacts(
            branch_name=branch_name,
            commit_message=commit_message,
            files={
                f"src/generated/{code.filename}": code.script,
                f"notebooks/{code.notebook_filename}": code.notebook,
                f"tests/generated/{tests.test_filename}": tests.test_file,
                f"tests/generated/{tests.conftest_filename}": tests.conftest,
                f"tests/fixtures/{tests.fixtures_filename}": json.dumps(
                    tests.fixtures, indent=2
                ),
            },
        )

        # Open PR
        pr = self._repo.create_pull(
            title=pr_desc.title,
            body=pr_desc.body,
            head=branch_name,
            base=self._base_branch,
        )

        # Add labels
        try:
            pr.add_to_labels("auto-generated", "etl-pipeline", "needs-review")
        except GithubException as exc:
            logger.warning("pr_label_failed", error=str(exc))

        logger.info(
            "pr_created",
            story_id=spec.story_id,
            pr_url=pr.html_url,
            pr_number=pr.number,
        )
        return PRResult(
            pr_url=pr.html_url,
            pr_number=pr.number,
            branch_name=branch_name,
            commit_sha=commit_sha,
        )

    # ------------------------------------------------------------------
    # Internal helpers — description generation
    # ------------------------------------------------------------------

    def _generate_pr_description(
        self,
        spec: ETLSpec,
        code: GeneratedCode,
        tests: GeneratedTests,
        test_results: dict | None,
    ) -> PRDescription:
        generated_files = [
            f"src/generated/{code.filename}",
            f"notebooks/{code.notebook_filename}",
            f"tests/generated/{tests.test_filename}",
            f"tests/generated/conftest.py",
            f"tests/fixtures/{tests.fixtures_filename}",
        ]

        user_prompt = self._prompts["pr_description"]["user"].format(
            story_json=json.dumps(
                {"id": spec.story_id, "title": spec.title, "summary": spec.summary},
                indent=2,
            ),
            etl_spec_json=json.dumps(spec.model_dump(), indent=2),
            generated_files="\n".join(f"- {f}" for f in generated_files),
            test_results=json.dumps(test_results or {}, indent=2),
        )

        return self._llm.complete_json(
            system_prompt=self._prompts["pr_description"]["system"],
            user_prompt=user_prompt,
            output_model=PRDescription,
        )

    def _generate_commit_message(self, spec: ETLSpec) -> str:
        user_prompt = self._prompts["commit_message"]["user"].format(
            story_id=spec.story_id,
            summary=spec.summary,
            operations=", ".join(op.type for op in spec.operations),
            files="script, notebook, tests, fixtures",
        )
        response = self._llm.complete(
            system_prompt=self._prompts["commit_message"]["system"],
            user_prompt=user_prompt,
            temperature=0.0,
        )
        return response.content.strip()

    # ------------------------------------------------------------------
    # Internal helpers — GitHub operations
    # ------------------------------------------------------------------

    def _make_branch_name(self, story_id: str) -> str:
        safe = story_id.lower().replace(" ", "-").replace("/", "-")
        return f"etl/auto/{safe}"

    def _create_branch(self, branch_name: str) -> None:
        base_ref = self._repo.get_branch(self._base_branch)
        try:
            self._repo.create_git_ref(
                ref=f"refs/heads/{branch_name}",
                sha=base_ref.commit.sha,
            )
            logger.info("branch_created", branch=branch_name)
        except GithubException as exc:
            if exc.status == 422:
                logger.warning("branch_already_exists", branch=branch_name)
            else:
                raise

    def _commit_artifacts(
        self,
        branch_name: str,
        commit_message: str,
        files: dict[str, str],
    ) -> str:
        """Commit multiple files to a branch in a single commit using the Git trees API."""
        base_ref = self._repo.get_git_ref(f"heads/{branch_name}")
        base_commit = self._repo.get_git_commit(base_ref.object.sha)
        base_tree = base_commit.tree

        # Create blobs for each file
        tree_elements = []
        for file_path, content in files.items():
            blob = self._repo.create_git_blob(content=content, encoding="utf-8")
            tree_elements.append(
                {
                    "path": file_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob.sha,
                }
            )

        new_tree = self._repo.create_git_tree(tree_elements, base_tree=base_tree)
        new_commit = self._repo.create_git_commit(
            message=commit_message,
            tree=new_tree,
            parents=[base_commit],
        )
        base_ref.edit(sha=new_commit.sha)
        logger.info(
            "artifacts_committed",
            branch=branch_name,
            commit_sha=new_commit.sha,
            files=list(files.keys()),
        )
        return new_commit.sha

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
