"""Read-only CodePipeline/CodeCommit queries for the bridge (run via uv, needs boto3).

  discover REPO...   pipelines whose CodeCommit source is one of REPOs: [{name, repo, branch}]
  poll               reads {"pipelines": [...], "known": [execution ids]} on stdin and prints
                     the latest executions of each pipeline; for executions it does not know yet
                     (running or newly finished) it adds the source commit's author email.

Credentials come from the normal AWS config (SSO). Exit code 3 with {"error": "sso_expired"}
means the SSO session expired and `aws sso login` is needed.

Usage: uv run --with boto3 python tools/aws_pipelines.py discover my-frontend my-backend
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

FINISHED = {"Succeeded", "Failed", "Stopped", "Superseded"}
SSO_ERRORS = ("SSOTokenLoadError", "UnauthorizedSSOTokenError", "TokenRetrievalError", "ExpiredToken",
              "SSOError", "NoCredentialsError", "InvalidGrantException")


def session():
    return boto3.Session(profile_name=os.environ.get("SMALLTV_AWS_PROFILE") or None)


def client(name: str):
    return session().client(name, config=Config(retries={"max_attempts": 8, "mode": "adaptive"}))


def source_of(pipeline: dict) -> dict | None:
    for stage in pipeline.get("stages", []):
        for action in stage.get("actions", []):
            if action.get("actionTypeId", {}).get("provider") == "CodeCommit":
                cfg = action.get("configuration", {})
                return {"repo": cfg.get("RepositoryName"), "branch": cfg.get("BranchName")}
    return None


def discover(repos: set[str]) -> list[dict]:
    cp = client("codepipeline")
    names = [p["name"] for page in cp.get_paginator("list_pipelines").paginate() for p in page["pipelines"]]

    def one(name: str):
        src = source_of(cp.get_pipeline(name=name)["pipeline"])
        return {"name": name, **src} if src and src["repo"] in repos else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        return sorted((p for p in pool.map(one, names) if p), key=lambda p: p["name"])


def poll(pipelines: list[dict], known: set[str]) -> list[dict]:
    cp, cc = client("codepipeline"), client("codecommit")

    def one(p: dict) -> list[dict]:
        out = []
        for e in cp.list_pipeline_executions(pipelineName=p["name"], maxResults=3)["pipelineExecutionSummaries"]:
            rev = (e.get("sourceRevisions") or [{}])[0]
            item = {
                "pipeline": p["name"], "repo": p["repo"], "branch": p.get("branch"),
                "id": e["pipelineExecutionId"], "status": e["status"],
                "start": e["startTime"].timestamp(), "updated": e["lastUpdateTime"].timestamp(),
                "commit": rev.get("revisionId"), "summary": rev.get("revisionSummary") or "",
            }
            if item["id"] not in known and item["commit"]:
                try:
                    commit = cc.get_commit(repositoryName=p["repo"], commitId=item["commit"])["commit"]
                    item["author"] = (commit.get("author") or {}).get("email", "")
                except ClientError:
                    item["author"] = ""
            out.append(item)
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:
        return [e for batch in pool.map(one, pipelines) for e in batch]


def main() -> None:
    try:
        if sys.argv[1:2] == ["discover"]:
            print(json.dumps(discover(set(sys.argv[2:]))))
        elif sys.argv[1:2] == ["poll"]:
            request = json.load(sys.stdin)
            print(json.dumps(poll(request["pipelines"], set(request.get("known", [])))))
        else:
            sys.exit(__doc__)
    except (BotoCoreError, ClientError) as exc:
        name = type(exc).__name__ + " " + str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if any(marker in name for marker in SSO_ERRORS) or "sso" in str(exc).lower():
            print(json.dumps({"error": "sso_expired"}))
            sys.exit(3)
        print(json.dumps({"error": type(exc).__name__}))
        sys.exit(1)


if __name__ == "__main__":
    main()
