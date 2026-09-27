#!/usr/bin/env python3
"""Audit LiteLLM /v1/models visibility for environment-supplied client keys."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Expectation:
    def __init__(self, environment_variable: str, models: tuple[str, ...]) -> None:
        self.environment_variable = environment_variable
        self.models = models


def parse_expectation(value: str) -> Expectation:
    if "=" not in value:
        raise ValueError("expectations must use ENVIRONMENT_VARIABLE=alias[,alias]")
    name, raw_models = value.split("=", 1)
    if not name or not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise ValueError("expectation key must be an environment variable name")
    models = tuple(item for item in raw_models.split(",") if item)
    if len(models) != len(set(models)):
        raise ValueError(f"{name} expectation contains duplicate aliases")
    return Expectation(name, models)


def visible_models(base_url: str, key: str, *, ca_file: str | None, timeout: float) -> set[str]:
    request = Request(
        base_url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        payload = json.load(response)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("/v1/models response has no data list")
    models: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("/v1/models response contains an invalid model entry")
        models.add(item["id"])
    return models


def audit(base_url: str, expectations: list[Expectation], *, ca_file: str | None, timeout: float) -> list[str]:
    failures: list[str] = []
    for expectation in expectations:
        key = os.environ.get(expectation.environment_variable)
        if not key:
            failures.append(f"{expectation.environment_variable}: credential is not set")
            continue
        try:
            actual = visible_models(base_url, key, ca_file=ca_file, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            # Never include request headers, URLs with credentials, or response bodies.
            failures.append(f"{expectation.environment_variable}: request failed ({type(exc).__name__})")
            continue
        expected = set(expectation.models)
        if actual != expected:
            failures.append(
                f"{expectation.environment_variable}: expected {sorted(expected)}, observed {sorted(actual)}"
            )
        else:
            print(f"{expectation.environment_variable}: model visibility matches ({len(actual)} alias(es))")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="LiteLLM API base URL")
    parser.add_argument("--expect", action="append", required=True, metavar="ENV=ALIASES")
    parser.add_argument("--ca-file", help="Optional PEM CA certificate for a private control-plane CA")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        expectations = [parse_expectation(value) for value in args.expect]
        failures = audit(args.base_url, expectations, ca_file=args.ca_file, timeout=args.timeout)
    except ValueError as exc:
        print(f"client-model-access: {exc}", file=sys.stderr)
        return 2
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
