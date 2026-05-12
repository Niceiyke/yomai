"""Research assistant agent — searches the web, fetches URLs, and summarizes."""
from __future__ import annotations

from yomai import Depends, RouteGroup, Yomai
from yomai.config import LLMConfig

from app.tools.search import web_search
from app.tools.summarize import fetch_url, summarize_text


def verify_api_key(request) -> None:
    """Simple per-route auth check."""
    from starlette.exceptions import HTTPException
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")


app = Yomai(
    llm=LLMConfig(max_tokens=512),
)


@app.agent(
    "/research",
    tools=[web_search, fetch_url, summarize_text],
    system=(
        "You are a research assistant. Use the provided tools to answer questions thoroughly.\n"
        "When a user asks about something factual, search the web first.\n"
        "If you find a relevant URL, you may fetch it to get more detail.\n"
        "Always cite your sources with the URLs you found."
    ),
    tags=["research", "v1"],
    summary="Research assistant — search, fetch, and summarize",
    cors={"allow_origins": ["http://localhost:3000"]},
)
async def research(message: str, session_id: str) -> None:
    pass


# Non-streaming endpoints for session management
@app.get(
    "/sessions/{session_id}",
    tags=["sessions"],
    summary="Get session message history",
    cors={"allow_origins": ["http://localhost:3000"]},
)
async def get_session(session_id: str) -> dict:
    """Return the message history for a session without calling the LLM."""
    history = await app.memory.load(session_id)
    return {
        "session_id": session_id,
        "message_count": len(history),
        "messages": history,
    }


@app.delete(
    "/sessions/{session_id}",
    tags=["sessions"],
    summary="Clear a session",
    dependencies=[Depends(verify_api_key)],
)
async def delete_session(session_id: str) -> dict:
    """Delete all messages for a session."""
    await app.memory.clear(session_id)
    return {"deleted": session_id, "message_count": 0}


# Versioned API group — shows how to version routes
v2 = RouteGroup("/v2", tags=["v2"], cors={"allow_origins": ["http://localhost:3000"]})


@v2.agent("/research", tools=[web_search, summarize_text])
async def research_v2(message: str, session_id: str) -> None:
    """V2 research agent — uses a different tool set."""
    pass


app.include_router(v2)