from __future__ import annotations

from typing import Any

from yomai.config import DevConfig


def build_openapi(
    routes_meta: list[dict[str, Any]],
    title: str = "Yomai Agent API",
    version: str = "1.0.0",
    api_key: str = "",
) -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    tool_schemas: dict[str, Any] = {}

    for route in routes_meta:
        path = route["path"]
        route_type = route.get("type", "agent")

        body_params = route.get("body_params", [])
        params = route.get("params", [])

        if route_type == "agent":
            body_props = {"message": {"type": "string", "description": "User message"}}
            for p in params:
                pn = p["name"]
                if pn == "session_id":
                    body_props["session_id"] = {"type": "string", "description": "Session identifier for memory persistence"}
                elif pn != "message":
                    body_props[pn] = _param_schema(p)

            request_body = {
                "required": ["message"],
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": body_props,
                            "required": ["message"],
                            "additionalProperties": False,
                        }
                    }
                },
            }

            responses = {
                "200": {
                    "description": "Server-Sent Event stream",
                    "content": {
                        "text/event-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                },
                "401": {"description": "Missing or invalid API key"},
                "503": {"description": "Server shutting down"},
            }

            post_spec: dict[str, Any] = {
                "summary": f"Yomai {route_type}: {path}",
                "tags": ["agents"],
                "requestBody": request_body,
                "responses": responses,
            }

            tools = route.get("tools", [])
            if tools:
                post_spec["x-yomai-tools"] = tools

            if api_key:
                post_spec["security"] = [{"ApiKeyAuth": []}]

        else:  # workflow
            body_props: dict[str, Any] = {}
            for p in params:
                body_props[p["name"]] = _param_schema(p)

            required = [p["name"] for p in params if p.get("required", True)]

            request_body = {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": body_props,
                            "required": required,
                            "additionalProperties": False,
                        }
                    }
                },
            }

            responses = {
                "200": {
                    "description": "Server-Sent Event stream",
                    "content": {
                        "text/event-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                },
                "401": {"description": "Missing or invalid API key"},
                "503": {"description": "Server shutting down"},
            }

            post_spec: dict[str, Any] = {
                "summary": f"Yomai {route_type}: {path}",
                "tags": ["workflows"],
                "requestBody": request_body,
                "responses": responses,
            }

            if api_key:
                post_spec["security"] = [{"ApiKeyAuth": []}]

        post_spec["x-yomai-type"] = route_type
        post_spec["x-yomai-path"] = path

        paths[path] = {"post": post_spec}

    schema: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version,
            "description": "Streaming LLM agent endpoints powered by Yomai.",
        },
        "paths": paths,
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "name": "Authorization",
                    "in": "header",
                    "description": "Set to `Bearer <your-yomai-api-key>`",
                }
            }
        },
    }

    return schema


def _param_schema(p: dict[str, Any]) -> dict[str, Any]:
    t = p.get("type", "string")
    schema: dict[str, Any] = {"type": t}
    default = p.get("default")
    if default is not None:
        schema["default"] = default
    return schema