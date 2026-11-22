"""GitHub REST API adapter with validated, read-first tools."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from core.connected_apps.native import NativeAppAdapter, NativeAppDescriptor, NativeTool


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


class GitHubAdapter(NativeAppAdapter):
    descriptor = NativeAppDescriptor(
        id="github",
        name="GitHub",
        description="Repositories, issues, pull requests, commits and source code.",
        category="development",
        icon="github",
        auth_provider="github",
        scopes=("repo", "read:user", "user:email"),
    )
    tools = (
        NativeTool("github_get_profile", "Get the authenticated GitHub user's profile.", _schema({})),
        NativeTool("github_list_repositories", "List repositories accessible to the authenticated user.", _schema({"limit":{"type":"integer","minimum":1,"maximum":100}})),
        NativeTool("github_search_repositories", "Search GitHub repositories by query.", _schema({"query":{"type":"string","minLength":1,"maxLength":256},"limit":{"type":"integer","minimum":1,"maximum":30}}, ("query",))),
        NativeTool("github_list_issues", "List issues for a repository. Repository must be owner/name.", _schema({"repository":{"type":"string","minLength":3,"maxLength":200},"state":{"type":"string","enum":["open","closed","all"]},"limit":{"type":"integer","minimum":1,"maximum":100}}, ("repository",))),
        NativeTool("github_get_issue", "Get a GitHub issue by repository and number.", _schema({"repository":{"type":"string","minLength":3,"maxLength":200},"number":{"type":"integer","minimum":1}}, ("repository","number"))),
        NativeTool("github_list_pull_requests", "List pull requests for a repository.", _schema({"repository":{"type":"string","minLength":3,"maxLength":200},"state":{"type":"string","enum":["open","closed","all"]},"limit":{"type":"integer","minimum":1,"maximum":100}}, ("repository",))),
        NativeTool("github_get_file", "Read a file from a repository at a ref.", _schema({"repository":{"type":"string","minLength":3,"maxLength":200},"path":{"type":"string","minLength":1,"maxLength":500},"ref":{"type":"string","maxLength":200}}, ("repository","path"))),
        NativeTool("github_create_issue", "Create an issue. Requires user confirmation.", _schema({"repository":{"type":"string","minLength":3,"maxLength":200},"title":{"type":"string","minLength":1,"maxLength":256},"body":{"type":"string","maxLength":65536},"labels":{"type":"array","items":{"type":"string"},"maxItems":20}}, ("repository","title")), read_only=False, requires_confirmation=True),
    )

    def _token(self, connection_id):
        token = self.store.get_credential(connection_id, "access_token")
        if not token:
            raise PermissionError("GitHub is not authorized for this connection")
        return token

    def _request(self, connection_id, method, path, query=None, payload=None):
        if not path.startswith("/") or ".." in path.split("/"):
            raise ValueError("Invalid GitHub API path")
        url = "https://api.github.com" + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token(connection_id)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "JARVIS-Connected-Apps",
            **({"Content-Type":"application/json"} if data is not None else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                body=response.read(2_000_000)
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                detail=json.loads(exc.read(8192)).get("message", "")
            except Exception:
                detail=""
            if exc.code in (401,403):
                raise PermissionError(f"GitHub authorization failed ({exc.code}): {detail or 'check token scopes'}") from None
            if exc.code == 404:
                raise LookupError("GitHub resource was not found or is not accessible") from None
            raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail[:300]}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"GitHub API connection failed: {type(exc).__name__}") from None

    @staticmethod
    def _repo(value):
        value=str(value or "").strip()
        parts=value.split("/")
        if len(parts)!=2 or any(not p or p in {".",".."} for p in parts):
            raise ValueError("repository must use owner/name format")
        return "/".join(urllib.parse.quote(p,safe="") for p in parts)

    def execute(self, connection_id, tool_name, arguments):
        args=dict(arguments or {})
        if tool_name=="github_get_profile":
            return self._request(connection_id,"GET","/user")
        if tool_name=="github_list_repositories":
            limit=max(1,min(int(args.get("limit",30)),100))
            return self._request(connection_id,"GET","/user/repos",{"per_page":limit,"sort":"updated","direction":"desc"})
        if tool_name=="github_search_repositories":
            limit=max(1,min(int(args.get("limit",10)),30))
            return self._request(connection_id,"GET","/search/repositories",{"q":args["query"],"per_page":limit})
        repo=self._repo(args.get("repository"))
        if tool_name=="github_list_issues":
            return self._request(connection_id,"GET",f"/repos/{repo}/issues",{"state":args.get("state","open"),"per_page":max(1,min(int(args.get("limit",30)),100))})
        if tool_name=="github_get_issue":
            return self._request(connection_id,"GET",f"/repos/{repo}/issues/{int(args['number'])}")
        if tool_name=="github_list_pull_requests":
            return self._request(connection_id,"GET",f"/repos/{repo}/pulls",{"state":args.get("state","open"),"per_page":max(1,min(int(args.get("limit",30)),100))})
        if tool_name=="github_get_file":
            path="/".join(urllib.parse.quote(part,safe="") for part in str(args["path"]).split("/") if part)
            query={"ref":args["ref"]} if args.get("ref") else None
            return self._request(connection_id,"GET",f"/repos/{repo}/contents/{path}",query)
        if tool_name=="github_create_issue":
            return self._request(connection_id,"POST",f"/repos/{repo}/issues",payload={"title":args["title"],"body":args.get("body",""),"labels":args.get("labels",[])})
        raise ValueError(f"Unknown GitHub tool: {tool_name}")

    def health_check(self, connection_id):
        self._request(connection_id,"GET","/user")
        return True
