"""Request boundary and metadata for the centrally managed LiteLLM routes."""

import asyncio
from contextvars import ContextVar
from functools import lru_cache
from ipaddress import ip_address, ip_network
import json
import logging
import os
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
import yaml

from litellm import UnsupportedParamsError
from litellm.integrations.custom_logger import CustomLogger


REQUEST_ROUTE = ContextVar("homecompute_request_route", default=None)
STRUCTURED_OUTPUT = ContextVar("homecompute_structured_output", default=False)
LOGGER = logging.getLogger("HomeCompute Routing")
NETWORK_KEY_OWNER = "lan-catalog-inference"
TEXT_PATHS = {"/chat/completions", "/responses", "/v1/chat/completions", "/v1/responses"}
CHAT_FIELDS = {
    "model", "messages", "stream", "stream_options", "tools", "tool_choice",
    "parallel_tool_calls", "response_format", "max_tokens", "max_completion_tokens",
    "temperature", "top_p", "frequency_penalty", "presence_penalty", "seed",
    "reasoning_effort", "store", "user", "n",
}
RESPONSE_FIELDS = {
    "model", "input", "instructions", "stream", "tools", "tool_choice",
    "parallel_tool_calls", "text", "max_output_tokens", "temperature", "top_p",
    "reasoning", "store", "truncation",
}
TEXT_CONTENT_TYPES = ("text", "input_text", "output_text", "refusal")
RESPONSE_ITEMS = ("message", "function_call", "function_call_output")
LOCAL_NETWORKS = tuple(map(ip_network, ("10.0.0.0/8", "127.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
MAX_BODY_BYTES = 32 * 1024 * 1024


@lru_cache(maxsize=1)
def load_routes():
    config_path = Path(os.environ.get("LITELLM_CONFIG", "/etc/litellm/config.yaml"))
    config = yaml.safe_load(config_path.read_text())
    routes = {}
    deployment_ids = set()
    for deployment in config["model_list"]:
        info = deployment.get("model_info", {})
        alias = info.get("homecompute_alias")
        if alias is None:
            continue
        if alias != deployment["model_name"]:
            raise ValueError("Managed deployment model name and policy alias must match")
        routes.setdefault(alias, info)
        deployment_ids.add(info["id"])

    forbidden_defaults = ("default_fallbacks", "fallbacks", "context_window_fallbacks", "content_policy_fallbacks")
    if routes and any(config.get(section, {}).get(field) for section in ("router_settings", "litellm_settings")
                      for field in forbidden_defaults):
        raise ValueError("Managed routes require fallback to be declared within their model group")

    for deployment in config["model_list"]:
        alias = deployment["model_name"]
        if alias not in routes:
            continue
        info = deployment.get("model_info", {})
        params = deployment["litellm_params"]
        fields = ("homecompute_alias", "homecompute_privacy", "homecompute_deadline", "homecompute_max_fallbacks")
        if any(info.get(field) != routes[alias][field] for field in fields):
            raise ValueError("All deployments in a managed route must share its policy")
        if routes[alias]["homecompute_privacy"] == "local_only":
            reference = params.get("api_base", "")
            if not params["model"].startswith("openai/") or not reference.startswith("os.environ/"):
                raise ValueError("Local route requires a declared private endpoint")
            endpoint = urlsplit(os.environ[reference.removeprefix("os.environ/")])
            if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                    or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                    or not any(ip_address(endpoint.hostname) in network for network in LOCAL_NETWORKS)):
                raise ValueError("Local route endpoint must use a private IP address")
    if "local-only" in routes and routes["local-only"]["homecompute_privacy"] != "local_only":
        raise ValueError("local-only requires local privacy")
    if routes and "homecompute_routing.policy" not in config["litellm_settings"].get("callbacks", []):
        raise ValueError("Managed routes require the HomeCompute authorization callback")
    return routes, deployment_ids


def requests_structured_output(body):
    text = body.get("text")
    output = text.get("format") if isinstance(text, dict) else body.get("response_format")
    return isinstance(output, dict) and output.get("type") in ("json_schema", "json_object")


def request_is_portable(body, *, responses):
    fields = RESPONSE_FIELDS if responses else CHAT_FIELDS
    if set(body) - fields or body.get("store") not in (None, False):
        return False
    count = body.get("n", 1)
    if type(count) is not int or count != 1 or body.get("truncation") not in (None, "disabled"):
        return False
    tools = body.get("tools")
    if tools is not None and (not isinstance(tools, list) or any(
        not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools
    )):
        return False
    choice = body.get("tool_choice")
    if choice is not None and not (
        isinstance(choice, str) and choice in {"auto", "none", "required"}
        or isinstance(choice, dict) and choice.get("type") == "function"
    ):
        return False
    items = body.get("input") if responses else body.get("messages")
    if responses and isinstance(items, str):
        return True
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict) or (responses and item.get("type", "message") not in RESPONSE_ITEMS):
            return False
        if item.get("type") == "function_call_output" and not isinstance(item.get("output"), str):
            return False
        content = item.get("content")
        if content is not None and not isinstance(content, str):
            if not isinstance(content, list) or any(
                not isinstance(part, dict) or part.get("type") not in TEXT_CONTENT_TYPES for part in content
            ):
                return False
    return True


async def json_error(scope, receive, send, status, message):
    response = JSONResponse({"error": {"message": message, "type": "homecompute_routing"}}, status_code=status)
    await response(scope, receive, send)


class RoutingBoundary:
    def __init__(self, app):
        self.app = app
        self.routes, _ = load_routes()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").rstrip("/")
        if scope["type"] != "http" or scope["method"] != "POST" or path not in TEXT_PATHS:
            return await self.app(scope, receive, send)

        buffer = bytearray()
        try:
            async for chunk in Request(scope, receive).stream():
                if len(buffer) + len(chunk) > MAX_BODY_BYTES:
                    return await json_error(scope, receive, send, 413, "Inference request is too large")
                buffer.extend(chunk)
        except ClientDisconnect:
            return
        payload = bytes(buffer)
        del buffer
        try:
            body = json.loads(payload)
        except (ValueError, RecursionError):
            body = None

        async def replay():
            nonlocal payload
            if payload is not None:
                message = {"type": "http.request", "body": payload, "more_body": False}
                payload = None
                return message
            return await receive()

        alias = body.get("model") if isinstance(body, dict) else None
        if not isinstance(alias, str) or alias not in self.routes:
            return await self.app(scope, replay, send)

        if not request_is_portable(body, responses=path.endswith("/responses")):
            return await json_error(
                scope, receive, send, 400,
                "Managed routes accept stateless text requests and client-executed function tools",
            )
        structured_token = STRUCTURED_OUTPUT.set(requests_structured_output(body))
        del body
        route = self.routes[alias]
        token = REQUEST_ROUTE.set(alias)
        started = False
        start = monotonic()
        status = None
        metadata = {}

        async def track_start(event):
            nonlocal started, status, metadata
            if event["type"] == "http.response.start":
                started = True
                status = event["status"]
                headers = [(key, value) for key, value in event["headers"] if key != b"x-litellm-model-api-base"]
                metadata = dict(headers)
                event = {**event, "headers": headers}
            await send(event)

        deadline = asyncio.timeout(route["homecompute_deadline"])
        try:
            async with deadline:
                await self.app(scope, replay, track_start)
        except TimeoutError:
            if not deadline.expired():
                raise
            status = 504
            if started:
                # Terminate the HTTP stream so the client receives an incomplete
                # response. A replacement could repeat an emitted tool call.
                raise
            await json_error(scope, receive, send, 504, "HomeCompute route deadline exceeded")
        finally:
            deployment = metadata.get(b"x-litellm-model-id", b"").decode("ascii", errors="replace")
            # Log only deployment IDs from the reviewed config and numeric values.
            if deployment not in load_routes()[1]:
                deployment = "unselected"
            retries = numeric_header(metadata, b"x-litellm-attempted-retries")
            fallbacks = numeric_header(metadata, b"x-litellm-attempted-fallbacks")
            LOGGER.info(
                "HomeCompute route alias=%s deployment=%s status=%s latency_ms=%d retries=%s fallbacks=%s",
                alias, deployment, status, (monotonic() - start) * 1000, retries, fallbacks,
            )
            REQUEST_ROUTE.reset(token)
            STRUCTURED_OUTPUT.reset(structured_token)


def numeric_header(metadata, name):
    value = metadata.get(name, b"")
    return int(value) if len(value) <= 8 and value.isdigit() else None


def has_explicit_cloud_grant(auth, alias):
    return (alias in (auth.models or []) and auth.user_id != NETWORK_KEY_OWNER
            and auth.key_alias != NETWORK_KEY_OWNER)


class RoutingPolicy(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        routes, ids = load_routes()
        requested = REQUEST_ROUTE.get()
        selected = data.get("model")
        managed = isinstance(selected, str) and (selected in routes or selected in ids)
        if not requested and not managed:
            return
        if requested is None or selected != requested or call_type not in {"completion", "acompletion", "aresponses"}:
            raise HTTPException(400, "Managed route requires a direct Chat Completions or Responses request")

        route = routes[requested]
        if route["homecompute_privacy"] == "cloud_allowed":
            if not has_explicit_cloud_grant(user_api_key_dict, requested):
                raise HTTPException(403, "Managed cloud route requires an explicitly scoped consumer key")

        for field in ("reasoning", "reasoning_effort", "router_settings_override", "api_base", "api_key",
                      "user_config", "custom_llm_provider", "timeout", "stream_timeout", "request_timeout",
                      "model_group_retry_policy", "provider_specific_header", "extra_headers",
                      "litellm_credential_name"):
            data.pop(field, None)
        data.update(
            fallbacks=[], context_window_fallbacks=[], content_policy_fallbacks=[],
            num_retries=0, max_fallbacks=route["homecompute_max_fallbacks"],
            disable_fallbacks=route["homecompute_max_fallbacks"] == 0,
            store=False,
        )
        return data

    async def async_filter_listed_models(self, user_api_key_dict, model_names):
        if user_api_key_dict.user_role == "proxy_admin":
            return model_names
        routes, _ = load_routes()
        return [
            alias for alias in model_names
            if alias not in routes or routes[alias]["homecompute_privacy"] == "local_only"
            or has_explicit_cloud_grant(user_api_key_dict, alias)
        ]

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        info = kwargs.get("model_info") or {}
        alias = info.get("homecompute_alias")
        if not alias and REQUEST_ROUTE.get() is None:
            return
        if alias != REQUEST_ROUTE.get():
            raise HTTPException(403, "Managed route deployment does not match authorized alias")
        if STRUCTURED_OUTPUT.get() and not info["homecompute_structured_output"]:
            raise UnsupportedParamsError(
                message="Deployment is not qualified for structured output",
                model=alias, llm_provider=info["homecompute_provider"],
            )
        effort = info["homecompute_effort"]
        if effort:
            if str(getattr(call_type, "value", call_type)) in {"responses", "aresponses"}:
                kwargs["reasoning"] = {"effort": effort}
            else:
                kwargs["reasoning_effort"] = effort
        return kwargs

    async def async_post_call_failure_deployment_hook(self, request_data, exception, call_type, fallback_depth=None):
        info = request_data.get("model_info") or {}
        if info.get("homecompute_alias"):
            LOGGER.warning(
                "HomeCompute deployment failure alias=%s deployment=%s depth=%s reason=%s status=%s",
                info["homecompute_alias"], info["id"], fallback_depth or 0, type(exception).__name__,
                getattr(exception, "status_code", None),
            )


class ManagedLogFilter(logging.Filter):
    def filter(self, record):
        # Upstream error messages can include request or response content.
        return REQUEST_ROUTE.get() is None


policy = RoutingPolicy()


def run():
    from litellm.proxy.proxy_server import app
    from litellm.proxy.proxy_cli import run_server

    proxy_logger = logging.getLogger("LiteLLM Proxy")
    for handler in proxy_logger.handlers:
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    log_filter = ManagedLogFilter()
    for name in ("LiteLLM Proxy", "LiteLLM Proxy.stdout", "LiteLLM Router", "LiteLLM"):
        logging.getLogger(name).addFilter(log_filter)
    app.add_middleware(RoutingBoundary)
    run_server()
