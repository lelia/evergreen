"""This file contains the main() and other functions needed to open an issue/PR dependabot is not enabled but could be"""

import uuid

import auth
import env
import github3
import requests

from datetime import datetime
from dependabot_file import build_dependabot_file


def main():  # pragma: no cover
    """Run the main program"""

    # Get the environment variables
    (
        organization,
        repository_list,
        token,
        ghe,
        exempt_repositories_list,
        follow_up_type,
        title,
        body,
        created_after_date,
        dry_run,
        commit_message,
        project_id,
    ) = env.get_env_vars()

    # Auth to GitHub.com or GHE
    github_connection = auth.auth_to_github(token, ghe)

    # If Project ID is set lookup the global project ID
    if project_id:
        # Check Organization is set as it is required for linking to a project
        if not organization:
            raise ValueError(
                "ORGANIZATION environment variable was not set. Please set it"
            )
        project_id = get_global_project_id(token, organization, project_id)

    # Get the repositories from the organization or list of repositories
    repos = get_repos_iterator(organization, repository_list, github_connection)

    # Iterate through the repositories and open an issue/PR if dependabot is not enabled
    count_eligible = 0
    for repo in repos:
        print("Checking " + repo.full_name)
        # Check all the things to see if repo is eligble for a pr/issue
        if repo.full_name in exempt_repositories_list:
            print("\tRepo found in list of exempt repositories")
            continue
        if repo.archived:
            print("\tRepo is marked as archived")
            continue
        try:
            if repo.file_contents(".github/dependabot.yml").size > 0:
                print("\tRepo already has Dependabot configuration")
                continue
        except github3.exceptions.NotFoundError:
            pass
        try:
            if repo.file_contents(".github/dependabot.yaml").size > 0:
                print("\tRepo already has Dependabot configuration")
                continue
        except github3.exceptions.NotFoundError:
            pass
        if created_after_date and repo.created_at.replace(
            tzinfo=None
        ) < datetime.strptime(created_after_date, "%Y-%m-%d"):
            print("\tCreated at date less than created after date")
            continue

        # Try to detect package managers and build a dependabot file
        dependabot_file = build_dependabot_file(repo)
        if dependabot_file is None:
            print("\tNo compatible package manager found")
            continue

        # If dry_run is set, just print the dependabot file
        if dry_run:
            if follow_up_type == "issue":
                skip = check_pending_issues_for_duplicates(title, repo)
                if not skip:
                    print("\tEligible for configuring dependabot.")
                    count_eligible += 1
                    print("\tConfiguration:\n" + dependabot_file)
            if follow_up_type == "pull":
                # Try to detect if the repo already has an open pull request for dependabot
                skip = check_pending_pulls_for_duplicates(title, repo)
                if not skip:
                    print("\tEligible for configuring dependabot.")
                    count_eligible += 1
                    print("\tConfiguration:\n" + dependabot_file)
            continue

        # Get dependabot security updates enabled if possible
        if not is_dependabot_security_updates_enabled(repo.owner, repo.name, token):
            enable_dependabot_security_updates(repo.owner, repo.name, token)
        if follow_up_type == "issue":
            skip = check_pending_issues_for_duplicates(title, repo)
            if not skip:
                count_eligible += 1
                body_issue = (
                    body
                    + "\n\n```yaml\n"
                    + "# .github/dependabot.yml\n"
                    + dependabot_file
                    + "\n```"
                )
                issue = repo.create_issue(title, body_issue)
                print("\tCreated issue " + issue.html_url)
                if project_id:
                    issue_id = get_global_issue_id(
                        token, organization, repo.name, issue.number
                    )
                    link_item_to_project(token, project_id, issue_id)
                    print("\tLinked issue to project " + project_id)
        else:
            count_eligible += 1
            # Try to detect if the repo already has an open pull request for dependabot
            skip = check_pending_pulls_for_duplicates(title, repo)

            # Create a dependabot.yaml file, a branch, and a PR
            if not skip:
                try:
                    pull = commit_changes(
                        title, body, repo, dependabot_file, commit_message
                    )
                    print("\tCreated pull request " + pull.html_url)
                    if project_id:
                        pr_id = get_global_pr_id(
                            token, organization, repo.name, pull.number
                        )
                        response = link_item_to_project(token, project_id, pr_id)
                        if response:
                            print("\tLinked pull request to project " + project_id)
                except github3.exceptions.NotFoundError:
                    print("\tFailed to create pull request. Check write permissions.")
                    continue

    print("Done! " + str(count_eligible) + " repositories were eligible.")


def is_dependabot_security_updates_enabled(owner, repo, access_token):
    """Check if Dependabot security updates are enabled at the /repos/:owner/:repo/automated-security-fixes endpoint using the requests library"""
    url = f"https://api.github.com/repos/{owner}/{repo}/automated-security-fixes"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.london-preview+json",
    }

    response = requests.get(url, headers=headers, timeout=20)
    if response.status_code == 200:
        return response.json()["enabled"]
    return False


def enable_dependabot_security_updates(owner, repo, access_token):
    """Enable Dependabot security updates at the /repos/:owner/:repo/automated-security-fixes endpoint using the requests library"""
    url = f"https://api.github.com/repos/{owner}/{repo}/automated-security-fixes"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.london-preview+json",
    }

    response = requests.put(url, headers=headers, timeout=20)
    if response.status_code == 204:
        print("\tDependabot security updates enabled successfully.")
    else:
        print("\tFailed to enable Dependabot security updates.")


def get_repos_iterator(organization, repository_list, github_connection):
    """Get the repositories from the organization or list of repositories"""
    repos = []
    if organization and not repository_list:
        repos = github_connection.organization(organization).repositories()
    else:
        # Get the repositories from the repository_list
        for repo in repository_list:
            repos.append(
                github_connection.repository(repo.split("/")[0], repo.split("/")[1])
            )

    return repos


def check_pending_pulls_for_duplicates(title, repo) -> bool:
    """Check if there are any open pull requests for dependabot and return the bool skip"""
    pull_requests = repo.pull_requests(state="open")
    skip = False
    for pull_request in pull_requests:
        if pull_request.head.ref.startswith(title):
            print("\tPull request already exists: " + pull_request.html_url)
            skip = True
            break
    return skip


def check_pending_issues_for_duplicates(title, repo) -> bool:
    """Check if there are any open issues for dependabot and return the bool skip"""
    issues = repo.issues(state="open")
    skip = False
    for issue in issues:
        if issue.title.startswith(title):
            print("\tIssue already exists: " + issue.html_url)
            skip = True
            break
    return skip


def commit_changes(title, body, repo, dependabot_file, message):
    """Commit the changes to the repo and open a pull reques and return the pull request object"""
    default_branch = repo.default_branch
    # Get latest commit sha from default branch
    default_branch_commit = repo.ref("heads/" + default_branch).object.sha
    front_matter = "refs/heads/"
    branch_name = "dependabot-" + str(uuid.uuid4())
    repo.create_ref(front_matter + branch_name, default_branch_commit)
    repo.create_file(
        path=".github/dependabot.yaml",
        message=message,
        content=dependabot_file.encode(),  # Convert to bytes object
        branch=branch_name,
    )

    pull = repo.create_pull(
        title=title, body=body, head=branch_name, base=repo.default_branch
    )
    return pull


def get_global_project_id(token, organization, number):
    """Fetches the project ID from GitHub's GraphQL API."""
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "query": f'query{{organization(login: "{organization}") {{projectV2(number: {number}){{id}}}}}}'
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None

    try:
        return response.json()["data"]["organization"]["projectV2"]["id"]
    except KeyError as e:
        print(f"Failed to parse response: {e}")
        return None


def get_global_issue_id(token, organization, repository, issue_number):
    """Fetches the issue ID from GitHub's GraphQL API"""
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "query": f"""
        query {{
          repository(owner: "{organization}", name: "{repository}") {{
            issue(number: {issue_number}) {{
              id
            }}
          }}
        }}
        """
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None

    try:
        return response.json()["data"]["repository"]["issue"]["id"]
    except KeyError as e:
        print(f"Failed to parse response: {e}")
        return None


def get_global_pr_id(token, organization, repository, pr_number):
    """Fetches the pull request ID from GitHub's GraphQL API"""
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "query": f"""
        query {{
          repository(owner: "{organization}", name: "{repository}") {{
            pullRequest(number: {pr_number}) {{
              id
            }}
          }}
        }}
        """
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None

    try:
        return response.json()["data"]["repository"]["pullRequest"]["id"]
    except KeyError as e:
        print(f"Failed to parse response: {e}")
        return None


def link_item_to_project(token, project_id, item_id):
    """Links an item (issue or pull request) to a project in GitHub."""
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "query": f'mutation {{addProjectV2ItemById(input: {{projectId: "{project_id}", contentId: "{item_id}"}}) {{item {{id}}}}}}'
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None


if __name__ == "__main__":
    main()  # pragma: no cover
