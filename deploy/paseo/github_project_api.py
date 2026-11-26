"""Read and validate GitHub Projects data for Paseo."""

import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


QUERY = """
query PaseoProject($owner: String!, $number: Int!, $filter: String!) {
  OWNER_KIND(login: $owner) {
    projectV2(number: $number) {
      closed
      items(first: 40, query: $filter) {
        pageInfo { hasNextPage }
        nodes {
          content {
            __typename
            ... on Issue { number state repository { nameWithOwner isPrivate } }
            ... on PullRequest { number state repository { nameWithOwner isPrivate } }
          }
          status: fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          priority: fieldValueByName(name: "Priority") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class GitHubProjectsError(ValueError):
    """A safe diagnostic that never contains a token or an upstream response body."""


def github_graphql(query, variables, token, *, allowed_scopes=("read:project",)):
    if not re.fullmatch(r"ghp_[A-Za-z0-9]+", token):
        raise GitHubProjectsError("Enter the full classic GitHub token beginning with ghp_.")
    request = Request("https://api.github.com/graphql", data=json.dumps({
        "query": query,
        "variables": variables,
    }).encode(), headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "HomeCompute-Paseo-Projects",
    })
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            scope_header = response.headers.get("X-OAuth-Scopes", "")
            scopes = {scope.strip() for scope in scope_header.split(",") if scope.strip()}
            if len(scopes) != 1 or not scopes.intersection(allowed_scopes):
                if "project" in scopes and allowed_scopes == ("read:project",):
                    raise GitHubProjectsError("GitHub reports write access to Projects. Select only the read:project scope.")
                expected = " or ".join(allowed_scopes)
                if scopes.intersection({"project", "read:project"}) and len(scopes) > 1:
                    raise GitHubProjectsError(f"GitHub reports additional permissions. Select only {expected}.")
                raise GitHubProjectsError(f"GitHub did not report the required scope. Select only {expected} on a classic token.")
            raw = response.read(256_001)
    except HTTPError as error:
        headers = error.headers or {}
        if error.code == 401:
            raise GitHubProjectsError(
                "GitHub rejected the token (HTTP 401). Check that it was copied in full and is still active."
            ) from None
        limited = headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in headers
        if error.code == 429 or (error.code == 403 and limited):
            raise GitHubProjectsError("GitHub is rate-limiting the request. Wait before retrying the same token.") from None
        raise GitHubProjectsError(f"GitHub returned HTTP {error.code} during the Projects request.") from None
    except (URLError, TimeoutError):
        raise GitHubProjectsError("Home Core could not complete the HTTPS request to GitHub.") from None
    if len(raw) > 256_000:
        raise ValueError("board response exceeds the size bound")
    data = json.loads(raw)
    if data.get("errors"):
        raise GitHubProjectsError("GitHub accepted the credential but rejected the Projects query.")
    return data["data"]


def project_items(entry, number, token, *, graphql=github_graphql):
    board = entry["github_project"]
    kind, owner = board["kind"], board["owner"]
    repository = entry["repository"]
    if (kind not in {"user", "organization"} or not re.fullmatch(r"[A-Za-z0-9-]+", owner)
            or type(number) is not int or number < 1
            or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)):
        raise ValueError("invalid board configuration")
    data = graphql(QUERY.replace("OWNER_KIND", kind), {
        "owner": owner, "number": number, "filter": f"repo:{repository} is:open",
    }, token)
    project = data[kind]["projectV2"]
    if project["closed"]:
        raise ValueError("board is closed")

    items = []
    for node in project["items"]["nodes"]:
        content = node["content"]
        if not content or content["__typename"] not in {"Issue", "PullRequest"}:
            continue
        repo = content["repository"]
        if (repo["isPrivate"] or repo["nameWithOwner"].casefold() != repository.casefold()
                or content["state"] != "OPEN"):
            continue
        selection = {
            key: (node[key] or {}).get("name", "")[:64]
            for key in ("status", "priority")
        }
        item_type = "issues" if content["__typename"] == "Issue" else "pull"
        items.append({
            "number": content["number"],
            "url": f"https://github.com/{repository}/{item_type}/{content['number']}",
            **selection,
        })
    return {"items": items, "more_items": project["items"]["pageInfo"]["hasNextPage"]}


def atomic_write(path, contents, mode, gid=None):
    with NamedTemporaryFile(dir=path.parent, prefix=".paseo-projects-", delete=False) as draft:
        temporary = Path(draft.name)
        try:
            if gid is not None:
                os.fchown(draft.fileno(), 0, gid)
            os.fchmod(draft.fileno(), mode)
            draft.write(contents)
            draft.flush()
            os.fsync(draft.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
