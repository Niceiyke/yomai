from __future__ import annotations

import asyncio
import enum
import uuid
from datetime import datetime
from typing import Any, cast

import httpx
import pytest
from pydantic import BaseModel

from yomai import Depends, RouteGroup, Yomai, tool
from yomai.config import LLMConfig, MemoryConfig
from yomai.testing import YomaiTestClient, mock_llm


# ─────────────────────────────────────────────────────────────────────────────
# Type coercion tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_path_params_injected() -> None:
    seen: dict[str, Any] = {}
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/chat/{session_id}/{model}")
    async def chat(message: str, session_id: str, model: str) -> None:
        seen.update({"message": message, "session_id": session_id, "model": model})

    with mock_llm(["ok"]):
        await YomaiTestClient(app).call("/chat/sid123/gpt-4", "hi", session_id="ignored")
    assert seen["session_id"] == "sid123"
    assert seen["model"] == "gpt-4"
    assert seen["message"] == "hi"


@pytest.mark.asyncio
async def test_get_path_params_injected() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/items/{item_id}")
    async def get_item(item_id: str, q: str | None = None) -> dict[str, Any]:
        return {"item_id": item_id, "q": q}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/items/abc123?q=filter")
    assert r.json() == {"item_id": "abc123", "q": "filter"}


@pytest.mark.asyncio
async def test_delete_path_params_injected() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    deleted: list[str] = []

    @app.delete("/items/{item_id}")
    async def delete_item(item_id: str) -> dict[str, str]:
        deleted.append(item_id)
        return {"deleted": item_id}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete("/items/foo")
    assert r.status_code == 200
    assert r.json() == {"deleted": "foo"}
    assert "foo" in deleted


@pytest.mark.asyncio
async def test_patch_path_params_and_body() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.patch("/items/{item_id}")
    async def update_item(item_id: str, name: str | None = None, qty: int = 0) -> dict[str, Any]:
        return {"item_id": item_id, "name": name, "qty": qty}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch("/items/42", json={"name": "updated", "qty": "7"})
    assert r.status_code == 200
    assert r.json()["name"] == "updated"
    assert r.json()["qty"] == 7  # type coerced from string


@pytest.mark.asyncio
async def test_uuid_type_coerced() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/items/{uid}")
    async def get_item(uid: uuid.UUID) -> dict[str, str]:
        return {"uid": str(uid)}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/items/550e8400-e29b-41d4-a716-446655440000")
    assert r.status_code == 200
    assert r.json()["uid"] == "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.asyncio
async def test_enum_type_coerced() -> None:
    class Status(enum.Enum):
        ACTIVE = "active"
        INACTIVE = "inactive"

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/status/{s}")
    async def get_status(s: Status) -> dict[str, str]:
        return {"status": s.value}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/status/active")
    assert r.status_code == 200
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_pydantic_model_body_coerced() -> None:
    class Item(BaseModel):
        name: str
        qty: int

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.put("/items/{item_id}")
    async def replace_item(item_id: str, item: Item) -> dict[str, Any]:
        return {"item_id": item_id, "name": item.name, "qty": item.qty}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.put("/items/5", json={"item": {"name": "widget", "qty": "3"}})
    assert r.status_code == 200
    assert r.json()["qty"] == 3  # qty coerced from string


@pytest.mark.asyncio
async def test_optional_missing_param_uses_default() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/search")
    async def search(q: str, limit: int = 10) -> dict[str, Any]:
        return {"q": q, "limit": limit}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/search?q=hello")
    assert r.json()["limit"] == 10
    r2 = await client.get("/search?q=hello&limit=5")
    assert r2.json()["limit"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# RouteGroup + include_router
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_group_prefix_applied() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    v1 = RouteGroup("/api/v1")

    @v1.agent("/chat")
    async def chat(message: str) -> None:
        pass

    app.include_router(v1)

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        routes_resp = await client.get("/__yomai__/routes")
    paths = [r["path"] for r in routes_resp.json()]
    assert "/api/v1/chat" in paths


@pytest.mark.asyncio
async def test_route_group_tags_merged() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    v1 = RouteGroup("/api/v1", tags=["v1", "internal"])

    @v1.agent("/chat", tags=["chat"])
    async def chat(message: str) -> None:
        pass

    app.include_router(v1)

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        routes_resp = await client.get("/__yomai__/routes")
    chat_meta = next(r for r in routes_resp.json() if r["path"] == "/api/v1/chat")
    assert "v1" in chat_meta["tags"]
    assert "internal" in chat_meta["tags"]
    assert "chat" in chat_meta["tags"]


@pytest.mark.asyncio
async def test_route_group_deprecated_inherited() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    v1 = RouteGroup("/api/v1", deprecated=True)

    @v1.agent("/chat")
    async def chat(message: str) -> None:
        pass

    @v1.agent("/chat-new", deprecated=False)
    async def chat_new(message: str) -> None:
        pass

    app.include_router(v1)

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        routes_resp = await client.get("/__yomai__/routes")
    by_path = {r["path"]: r for r in routes_resp.json()}
    assert by_path["/api/v1/chat"]["deprecated"] is True
    assert by_path["/api/v1/chat-new"]["deprecated"] is False


@pytest.mark.asyncio
async def test_route_group_cors_applied() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    v1 = RouteGroup("/api", cors={"allow_origins": ["https://app.example.com"]})

    @v1.get("/info")
    async def info() -> dict[str, str]:
        return {"info": "ok"}

    app.include_router(v1)

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/info")
    assert r.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"


@pytest.mark.asyncio
async def test_route_group_workflow_registered() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    v1 = RouteGroup("/api/v1")

    @v1.workflow("/search")
    async def search(topic: str, runner=None) -> str:
        return topic

    app.include_router(v1)

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        routes_resp = await client.get("/__yomai__/routes")
    paths = [r["path"] for r in routes_resp.json()]
    assert "/api/v1/search" in paths


@pytest.mark.asyncio
async def test_route_group_prefix_must_start_with_slash() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    v1 = RouteGroup("api/v1")  # missing leading slash
    try:
        app.include_router(v1)
        assert False, "should have raised"
    except Exception as e:
        assert "prefix" in str(e).lower()


# ─────────────────────────────────────────────────────────────────────────────
# Depends / dependency injection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_depends_runs_before_handler() -> None:
    call_order: list[str] = []

    def checker(request) -> None:
        call_order.append("dependency")
        # Cannot raise — just run

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/check", dependencies=[Depends(checker)])
    async def check() -> dict[str, list[str]]:
        call_order.append("handler")
        return {"order": call_order}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/check")
    assert r.status_code == 200
    assert r.json()["order"] == ["dependency", "handler"]


@pytest.mark.asyncio
async def test_depends_short_circuit() -> None:
    from starlette.exceptions import HTTPException

    def auth_fail(request) -> None:
        raise HTTPException(401, "nope")

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/protected", dependencies=[Depends(auth_fail)])
    async def protected() -> dict[str, str]:
        return {"ok": "true"}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/protected")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_depends_async_callable() -> None:
    seen: list[str] = []

    async def async_dep(request) -> None:
        seen.append("async_dep")

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/async-dep", dependencies=[Depends(async_dep)])
    async def async_dep_route() -> dict[str, list[str]]:
        seen.append("handler")
        return {"seen": seen}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/async-dep")
    assert r.status_code == 200
    assert r.json()["seen"] == ["async_dep", "handler"]


# ─────────────────────────────────────────────────────────────────────────────
# Per-route CORS
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_route_cors_headers() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/cors-test", cors={"allow_origins": ["https://front.example.com"], "allow_credentials": True})
    async def cors_test() -> dict[str, str]:
        return {"cors": "ok"}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/cors-test")
    assert r.headers.get("Access-Control-Allow-Origin") == "https://front.example.com"
    assert r.headers.get("Access-Control-Allow-Credentials") == "true"


@pytest.mark.asyncio
async def test_per_route_cors_overrides_group_cors() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    v1 = RouteGroup("/api", cors={"allow_origins": ["https://group.example.com"]})

    @v1.get("/override", cors={"allow_origins": ["https://override.example.com"]})
    async def override() -> dict[str, str]:
        return {"ok": "ok"}

    app.include_router(v1)

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/override")
    assert r.headers.get("Access-Control-Allow-Origin") == "https://override.example.com"


# ─────────────────────────────────────────────────────────────────────────────
# Route metadata — tags, summary, description, deprecated
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openapi_deprecated_flag() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/old", deprecated=True)
    async def old_endpoint() -> dict[str, str]:
        return {"ok": "ok"}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        schema = (await client.get("/__yomai__/openapi.json")).json()
    assert schema["paths"]["/old"]["get"]["deprecated"] is True


@pytest.mark.asyncio
async def test_openapi_custom_summary_and_description() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get(
        "/custom",
        summary="My custom endpoint",
        description="Longer description of what this does",
        tags=["custom", "v2"],
    )
    async def custom() -> dict[str, str]:
        return {"ok": "ok"}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        schema = (await client.get("/__yomai__/openapi.json")).json()
    op = schema["paths"]["/custom"]["get"]
    assert op["summary"] == "My custom endpoint"
    assert op["description"] == "Longer description of what this does"
    assert "custom" in op["tags"]
    assert "v2" in op["tags"]


@pytest.mark.asyncio
async def test_openapi_path_params_in_schema() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/users/{user_id}/posts/{post_id}")
    async def get_post(user_id: str, post_id: str) -> dict[str, str]:
        return {"user_id": user_id, "post_id": post_id}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        schema = (await client.get("/__yomai__/openapi.json")).json()
    params = schema["paths"]["/users/{user_id}/posts/{post_id}"]["get"]["parameters"]
    param_names = [p["name"] for p in params]
    assert "user_id" in param_names
    assert "post_id" in param_names
    path_params = [p for p in params if p["in"] == "path"]
    assert len(path_params) == 2


@pytest.mark.asyncio
async def test_openapi_non_streaming_routes_have_get_delete_methods() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.get("/items")
    async def list_items() -> list[str]:
        return []

    @app.delete("/items/{id}")
    async def delete_item(id: str) -> dict[str, str]:
        return {"deleted": id}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        schema = (await client.get("/__yomai__/openapi.json")).json()
    assert "get" in schema["paths"]["/items"]
    assert "delete" in schema["paths"]["/items/{id}"]


# ─────────────────────────────────────────────────────────────────────────────
# HEAD and OPTIONS
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_head_returns_200() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.head("/exists/{id}")
    async def head_exists(id: str) -> None:
        pass

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.head("/exists/abc")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_options_returns_cors_headers() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    @app.options("/preflight", cors={"allow_origins": ["https://example.com"], "allow_methods": ["POST"]})
    async def preflight() -> None:
        pass

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options("/preflight")
    assert r.status_code == 200
    assert "Access-Control-Allow-Origin" in r.headers
    assert "Access-Control-Allow-Methods" in r.headers