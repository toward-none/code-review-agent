import asyncio
import os

from collections import defaultdict
from functools import cached_property
from typing import Any

import dotenv

from github import Auth, Github, PullRequest, PullRequestReview, Repository as GithubRepository
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


dotenv.load_dotenv()
print(os.environ.items())


class PRDetails(BaseModel):
    author: str
    title: str
    diff_url: str
    state: str
    head_sha: str
    commit_SHAs: list[str]
    body: str | None = ""


class IgnoredArgs(BaseModel):
    """Base for tool argument models; ignores extra args from the LLM."""
    model_config = ConfigDict(extra="ignore")


class CommitFile(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: str | None


class ReviewDraft(BaseModel):
    review_markdown: str


class FinalReview(BaseModel):
    review_markdown: str


def create_github_client() -> Github:
    return Github(auth=Auth.Token(os.getenv("GITHUB_TOKEN", ""))) if os.getenv("GITHUB_TOKEN") else Github()


class Repository:
    def __init__(self, client: Github, full_repo_name: str) -> None:
        self.client = client
        self.remote_repository: GithubRepository = self.client.get_repo(full_repo_name)


class ReviewDeps:
    """Light facade for providing all needed data and interactions in agent context."""

    def __init__(self, pull_request_number: int, repository: Repository) -> None:
        self.pull_request_number = pull_request_number
        self.repository = repository
        self.state =  defaultdict(dict)

    @cached_property
    def remote_repository(self) -> GithubRepository:
        return self.repository.remote_repository

    @cached_property
    def pull_request(self) -> PullRequest:
        return self.remote_repository.get_pull(self.pull_request_number)

    def get_commit_by_sha(self, sha: str) -> Any:
        return self.remote_repository.get_commit(sha)

    def create_inline_comment(self, comment, commit_sha: str, path: str, line: int) -> PullRequestReview:
        return self.pull_request.create_review_comment(
            body=comment,
            commit=self.get_commit_by_sha(sha=commit_sha),
            path=path,
            line=line,
        )

    def create_review(self, comment: str) -> PullRequestReview:
        return self.pull_request.create_review(body=comment, event="COMMENT")

    def get_file_contents(self, file_name: str) -> str:
        print(file_name)
        file_content = self.remote_repository.get_contents(file_name).decoded_content.decode("utf-8")
        self.state["read_files_contents"][file_name] = file_content
        return file_content


llm_model = OpenAIChatModel(
    model_name=os.getenv("OPENAI_MODEL", ""),
    provider=OpenAIProvider(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL", ""),
    ),
)


review_agent = Agent(
    model=llm_model,
    system_prompt=(
        "You orchestrate PR review workflow by calling the available tools and following the instructions. "
        "First gather context, then draft a review, then refine and finally post it."
    ),
    deps_type=ReviewDeps,
)


@review_agent.tool
def pr_commits_details(ctx: RunContext[ReviewDeps], head_sha: str) -> list[CommitFile]:
    """PR commits details tool — given the commit SHA returns info about changed files."""
    commit = ctx.deps.get_commit_by_sha(head_sha)
    commited_files = [
        CommitFile(
            filename=file.filename,
            status=file.status,
            additions=file.additions,
            deletions=file.deletions,
            changes=file.changes,
            patch=file.patch,
        )
        for file in commit.files
    ]
    ctx.deps.state["gathered_context"].update(commited_files=commited_files)
    return commited_files


@review_agent.tool
def fetch_pr_details(ctx: RunContext[ReviewDeps], args: IgnoredArgs) -> PRDetails:
    """PR details tool — returns details about the pull request."""
    pull_request = ctx.deps.pull_request
    pull_request_details =  PRDetails(
        author=pull_request.user.login,
        title=pull_request.title,
        body=pull_request.body,
        diff_url=pull_request.diff_url,
        state=pull_request.state,
        head_sha=pull_request.head.sha,
        commit_SHAs=[commit.sha for commit in pull_request.get_commits()],
    )
    ctx.deps.state["gathered_context"].update(pull_request_details=pull_request_details)
    return pull_request_details


@review_agent.tool
def fetch_github_file(ctx: RunContext[ReviewDeps], file_name: str) -> str:
    """Fetching file content from GitHub by its name."""
    return ctx.deps.get_file_contents(file_name)


@review_agent.tool
def create_inline_comment(
    ctx: RunContext[ReviewDeps],
    comment: str,
    commit_sha: str,
    path: str,
    line: int,
) -> None:
    """
    Create an inline review comment on a specific line in the diff.

    `line` is the line index in the diff for that file (1-based),
    not the absolute line number in the file.
    `commit_sha` is the SHA of the commit for which the comment is created.
    """
    ctx.deps.create_inline_comment(
        comment=comment,
        commit_sha=commit_sha,
        path=path,
        line=line,
    )


@review_agent.tool
def post_review(ctx: RunContext[ReviewDeps], comment: str) -> None:
    """Posts the final review comment on the pull request."""
    ctx.deps.create_review(comment=comment)


CONTEXT_AGENT_PROMPT = """
You are the context gathering helper. You must execute ALL of the following steps Do not add any extra arguments
 to the tools functions.

1. Call `fetch_pr_details` to get PR metadata.
2. Call `pr_commits_details` using the head_sha from step 1.
3. Call `fetch_github_file` with filename "README.md" to get general context of the project.

4. Check which lines could be improved? Quote them and offer concrete suggestions.
- Use the provided context and draft short inline comments but no more than 10.
- Every inline comment should be followed by code example.

5. For each prepared comments post it using `call post_inline_comment(comment=..., commit=..., path=..., line=...)..`

6. Then make summary comment  ~200-500 word review in markdown covering:
- What is good about the PR?
- Are there tests for new functionality?
- Are there any structural improvements needed ?
- Should SOLID or other good practices be used ?
- Any design patterns would be better to use ?

7. Post Summary using `post_review`.
"""


REVIEW_AGENT_PROMPT = """
You are the Review and Posting helper. You receive a draft review and must:

1. Verify that the review:
   - Is ~200-300 words in markdown format.
   - Specifies what is good about the PR.
   - Mentions whether the author followed ALL contribution rules and what is missing.
   - Notes test coverage / migrations for new models.
   - Notes documentation for new endpoints.
   - Suggests which lines could be improved (with quoted lines).
2. If anything is missing, rewrite the review to satisfy all criteria.
3. Once the review is satisfactory, return the final review markdown.
"""


async def run_review_workflow() -> None:
    """
    High-level workflow:
      1. Gather context.
      2. Draft review (inline comments).
      3. Draft summary and post review.
    """
    pr_number = int(os.getenv("PR_NUMBER") or "0")
    with create_github_client() as github_client:
        review_deps = ReviewDeps(
            repository=Repository(
                client=github_client,
                full_repo_name=os.getenv("REPOSITORY") or "",  # could use Pydantic settings
            ),
            pull_request_number=pr_number,
        )

        async with review_agent.run_stream(
            f"Write code review for PR {pr_number}. {CONTEXT_AGENT_PROMPT}", deps=review_deps
        ) as response:
            async for text in response.stream_text():
                print(text)


async def main() -> None:
    await run_review_workflow()


if __name__ == "__main__":
    asyncio.run(main())
