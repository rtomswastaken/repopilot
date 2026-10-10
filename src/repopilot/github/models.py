"""GitHub domain models."""

from typing import List, Optional
from pydantic import BaseModel, Field


class GitHubRepoInfo(BaseModel):
    owner: str
    name: str
    default_branch: str = "main"
    is_private: bool = False
    description: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class GitHubIssue(BaseModel):
    number: int
    title: str
    body: str = ""
    state: str = "open"
    labels: List[str] = Field(default_factory=list)


class GitHubPullRequest(BaseModel):
    number: int
    html_url: str
    title: str
    head_branch: str
    base_branch: str
    state: str = "open"
    draft: bool = False
    merged: bool = False


class GitHubCommit(BaseModel):
    sha: str
    message: str
    author: str = ""
