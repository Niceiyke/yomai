from __future__ import annotations

from typing import Any


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
        tags = route.get("tags", ["agents"] if route_type == "agent" else ["workflows"])
        summary = route.get("summary", f"Yomai {route_type}: {path}")
        description = route.get("description", "")
        deprecated = route.get("deprecated", False)
        path_params = route.get("path_params", [])
        route.get("body_params", [])
        params = route.get("params", [])

        if route_type == "agent":
            body_props: dict[str, Any] = {"message": {"type": "string", "description": "User message"}}
            for p in params:
                pn = p["name"]
                if pn == "session_id":
                    body_props["session_id"] = {"type": "string", "description": "Session identifier for memory persistence"}
                elif pn not in path_params:
                    body_props[pn] = _param_schema(p)

            # Build OpenAPI parameter list for path params
            openapi_params: list[dict[str, Any]] = []
            for pp in path_params:
                param_info = next((p for p in params if p["name"] == pp), None)
                if param_info:
                    openapi_params.append({
                        "name": pp,
                        "in": "path",
                        "required": True,
                        "schema": _param_schema(param_info),
                        "description": f"Path parameter: {pp}",
                    })

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
                "summary": summary,
                "tags": tags,
                "description": description,
                "deprecated": deprecated,
                "requestBody": request_body,
                "responses": responses,
            }
            if openapi_params:
                post_spec["parameters"] = openapi_params

            tools = route.get("tools", [])
            if tools:
                post_spec["x-yomai-tools"] = tools
            for tool_schema in route.get("tool_schemas", []):
                if isinstance(tool_schema, dict) and isinstance(tool_schema.get("name"), str):
                    tool_schemas[tool_schema["name"]] = {
                        "type": "object",
                        "description": tool_schema.get("description", ""),
                        "properties": tool_schema.get("properties", {}),
                        "required": tool_schema.get("required", []),
                    }

            if api_key:
                post_spec["security"] = [{"ApiKeyAuth": []}]

        elif route_type in ("get", "delete", "head", "options"):
            # Non-streaming routes
            openapi_params: list[dict[str, Any]] = []
            for p in params:
                pn = p["name"]
                param_in = "path" if p.get("in") == "path" else "query"
                openapi_params.append({
                    "name": pn,
                    "in": param_in,
                    "required": p.get("required", True),
                    "schema": _param_schema(p),
                    "description": f"{'Path' if param_in == 'path' else 'Query'} parameter: {pn}",
                })

            responses = {
                "200": {"description": "Successful response"},
                "401": {"description": "Missing or invalid API key"},
                "503": {"description": "Server shutting down"},
            }

            method = route_type if route_type != "options" else "get"
            post_spec = {
                "summary": summary,
                "tags": tags,
                "description": description,
                "deprecated": deprecated,
                "responses": responses,
            }
            if openapi_params:
                post_spec["parameters"] = openapi_params
            if api_key:
                post_spec["security"] = [{"ApiKeyAuth": []}]

            paths[path] = {method: post_spec}
            continue

        else:  # workflow
            body_props: dict[str, Any] = {}
            for p in params:
                pn = p["name"]
                if pn not in path_params:
                    body_props[pn] = _param_schema(p)

            required = [p["name"] for p in params if p.get("required", True) and p.get("in") != "path"]

            openapi_params: list[dict[str, Any]] = []
            for pp in path_params:
                param_info = next((p for p in params if p["name"] == pp), None)
                if param_info:
                    openapi_params.append({
                        "name": pp,
                        "in": "path",
                        "required": True,
                        "schema": _param_schema(param_info),
                        "description": f"Path parameter: {pp}",
                    })

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

            post_spec = {
                "summary": summary,
                "tags": tags,
                "description": description,
                "deprecated": deprecated,
                "requestBody": request_body,
                "responses": responses,
            }
            if openapi_params:
                post_spec["parameters"] = openapi_params

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
            },
            "schemas": {f"Tool_{name}": schema for name, schema in tool_schemas.items()},
        },
    }

    return schema


def _param_schema(p: dict[str, Any]) -> dict[str, Any]:
    t = p.get("type", "string")
    schema: dict[str, Any] = {"type": t}
    default = p.get("default")
    if default is not None:
        schema["default"] = default
    # Handle format hints based on name (heuristic)
    name = p.get("name", "")
    if name.endswith("_id") or name == "uuid":
        schema["format"] = "uuid"
    elif name.endswith("_at") or name == "timestamp" or name == "datetime":
        schema["format"] = "date-time"
    return schema


def _build_params(params: list[dict[str, Any]], path_params: list[str]) -> list[dict[str, Any]]:
    """Build OpenAPI parameter list from route params, separating path and query."""
    openapi_params: list[dict[str, Any]] = []
    for p in params:
        pn = p["name"]
        param_in = "path" if pn in path_params else "query"
        openapi_params.append({
            "name": pn,
            "in": param_in,
            "required": p.get("required", True),
            "schema": _param_schema(p),
        })
    return openapi_params
