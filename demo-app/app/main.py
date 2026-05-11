"""Entry point — import agents and run the Yomai app."""
from app.agents.researcher import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)