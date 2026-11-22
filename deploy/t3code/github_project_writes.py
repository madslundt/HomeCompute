"""Handle the coding team's two GitHub Projects mutations on a local socket."""

import json
from socketserver import StreamRequestHandler, UnixStreamServer


MAX_REQUEST = 2048
TARGET = """
query T3ProjectTarget($owner: String!, $repo: String!, $board: Int!, $number: Int!,
                      $field: String!, $edit: Boolean!, $cursor: String) {
  viewer { login }
  user(login: $owner) {
    projectV2(number: $board) {
      id closed viewerCanUpdate
      owner { __typename ... on User { login } }
      selection: field(name: $field) @include(if: $edit) {
        ... on ProjectV2SingleSelectField { id options { id name } }
      }
    }
  }
  repository(owner: $owner, name: $repo) {
    nameWithOwner isPrivate
    issueOrPullRequest(number: $number) {
      ... on Issue { id state projectItems(first: 30, after: $cursor, includeArchived: true) { ...Membership } }
      ... on PullRequest { id state projectItems(first: 30, after: $cursor, includeArchived: true) { ...Membership } }
    }
  }
}
fragment Membership on ProjectV2ItemConnection {
  nodes { id isArchived project { id } }
  pageInfo { hasNextPage endCursor }
}
"""
ADD = """
mutation T3AddItem($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""
SET = """
mutation T3SetField($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}
  }) { projectV2Item { id } }
}
"""


class WriteError(ValueError):
    """An error safe to return to the T3 runner."""


def validate_request(request, repos):
    if not isinstance(request, dict):
        raise WriteError("Expected one GitHub Projects request.")
    action = request.get("action")
    common = {"action", "repository", "board", "number"}
    if action == "add":
        expected = common
    elif action == "set":
        expected = common | {"field", "value"}
    else:
        raise WriteError("Supported GitHub Projects actions are add and set.")
    if request.keys() != expected or request["repository"] not in repos:
        raise WriteError("Use a configured T3 repository and the documented command arguments.")
    for key in ("board", "number"):
        if type(request[key]) is not int or not 0 < request[key] < 2**31:
            raise WriteError("Use a positive GitHub board and issue or pull request number.")
    if action == "set":
        value = request["value"]
        if request["field"] not in ("Status", "Priority") or not isinstance(value, str) or not 0 < len(value) <= 128:
            raise WriteError("Provide a Status or Priority field and its option name.")


def resolve_target(graphql, owner, request):
    repository = request["repository"]
    variables = {
        "owner": owner, "repo": repository.split("/")[1], "board": request["board"],
        "number": request["number"], "field": request.get("field", ""),
        "edit": request["action"] == "set", "cursor": None,
    }
    cursors = set()
    for _ in range(5):
        data = graphql(TARGET, variables)
        board = (data["user"] or {}).get("projectV2")
        if (data["viewer"]["login"].casefold() != owner or not board or board["closed"]
                or not board["viewerCanUpdate"] or board["owner"]["__typename"] != "User"
                or board["owner"]["login"].casefold() != owner):
            raise WriteError("Choose an open, editable board owned by the configured GitHub account.")
        repo = data["repository"]
        if not repo or repo["isPrivate"] or repo["nameWithOwner"].casefold() != repository.casefold():
            raise WriteError("Choose a public repository from the T3 configuration.")
        content = repo["issueOrPullRequest"]
        if not content or content["state"] != "OPEN":
            raise WriteError("Choose an open issue or pull request.")
        membership = content["projectItems"]
        for item in membership["nodes"]:
            if item and item["project"]["id"] == board["id"]:
                if item["isArchived"]:
                    raise WriteError("Restore the archived item in GitHub before updating it.")
                return board, content, item["id"]
        page = membership["pageInfo"]
        if not page["hasNextPage"]:
            return board, content, None
        cursor = page["endCursor"]
        if not cursor or cursor in cursors:
            break
        cursors.add(cursor)
        variables = {**variables, "cursor": cursor}
    raise WriteError("Could not verify the item's membership on this board.")


def write_project(graphql, owner, request):
    board, content, item_id = resolve_target(graphql, owner, request)
    if request["action"] == "add":
        if item_id:
            return "Item is already on the board."
        result = graphql(ADD, {"project": board["id"], "content": content["id"]})
        if result["addProjectV2ItemById"]["item"]["id"]:
            return "Added item to the board."
    else:
        if not item_id:
            raise WriteError("Add the item to this board before setting a field.")
        field = board.get("selection")
        if not field or not field.get("id"):
            raise WriteError("This board needs a single-select field with that name.")
        options = [option for option in field["options"] if option["name"] == request["value"]]
        if len(options) != 1:
            raise WriteError("Use an exact option name configured for that board field.")
        result = graphql(SET, {
            "project": board["id"], "item": item_id, "field": field["id"], "option": options[0]["id"],
        })
        if result["updateProjectV2ItemFieldValue"]["projectV2Item"]["id"] == item_id:
            return f"Updated {request['field']} on the board."
    raise WriteError("GitHub could not confirm the update. Check the board before retrying.")


class WriteHandler(StreamRequestHandler):
    timeout = 3

    def handle(self):
        try:
            raw = self.rfile.readline(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                raise WriteError("The GitHub Projects request is incomplete or too large.")
            request = json.loads(raw)
            result = {"ok": True, "message": self.server.apply(request)}
        except (WriteError, self.server.github_error) as error:
            result = {"ok": False, "message": str(error)}
        except (OSError, ValueError, KeyError, TypeError):
            result = {"ok": False, "message": "GitHub Projects could not confirm the request. Check the board before retrying."}
        try:
            self.wfile.write(json.dumps(result).encode() + b"\n")
        except OSError:
            pass


def serve_writes(socket_path, graphql, github_error, credential, owner, repos, updated):
    def apply(request):
        validate_request(request, repos)
        try:
            token = credential.read_text().strip()
        except FileNotFoundError:
            raise WriteError("GitHub Projects write access requires a provisioned project token.") from None

        def query(document, variables):
            try:
                return graphql(document, variables, token, allowed_scopes=("project",))
            finally:
                # A failed response can follow a successful GitHub mutation.
                if document in (ADD, SET):
                    updated.set()

        return write_project(query, owner, request)

    socket_path.unlink(missing_ok=True)
    server = UnixStreamServer(str(socket_path), WriteHandler)
    server.apply = apply
    server.github_error = github_error
    # The two containers share this directory through bind mounts. The T3
    # mount stays read-only and the host parent is accessible only to root.
    socket_path.chmod(0o666)
    return server
