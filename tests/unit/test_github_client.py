"""Unit tests for GitHub client operations and mock behavior."""

from repopilot.github.client import MockGitHubClient


def test_mock_github_client_branch_and_pr():
    client = MockGitHubClient()
    owner = "test-owner"
    name = "test-repo"

    repo = client.get_repo(owner, name)
    assert repo.owner == owner
    assert repo.name == name
    assert repo.default_branch == "main"

    issues = client.list_open_issues(owner, name)
    assert isinstance(issues, list)

    branch_created = client.create_branch(owner, name, "main", "feature/my-branch")
    assert branch_created is True

    pr = client.create_pull_request(
        owner=owner,
        name=name,
        title="Add feature",
        body="Description of feature",
        head_branch="feature/my-branch",
        base_branch="main",
    )
    assert pr.number >= 100
    assert pr.state == "open"
    assert pr.merged is False

    merged = client.merge_pull_request(owner, name, pr.number, "Merge feature PR")
    assert merged is True
    assert pr.merged is True
    assert pr.state == "closed"
