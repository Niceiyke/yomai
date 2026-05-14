"""Dataset loading, validation, and representation for agent evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    name: str = ""
    message: str | list[dict[str, Any]]
    session_id: str | None = None
    expected_output: str | None = None
    expected_tools: list[dict[str, Any]] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    rubric: str | None = None
    min_tokens: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalDataset(BaseModel):
    name: str = "dataset"
    description: str = ""
    cases: list[EvalCase]


def load_dataset(path: Path | str) -> EvalDataset:
    path = Path(path)
    if path.suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    if path.suffix == ".json":
        return _load_json(path)
    raise ValueError(f"Unsupported dataset format: {path.suffix}")


def _load_json(path: Path) -> EvalDataset:
    data = json.loads(path.read_text())
    cases_data = data if isinstance(data, list) else data.get("cases", data.get("tests", []))
    meta = {} if isinstance(data, list) else {k: v for k, v in data.items() if k not in ("cases", "tests")}
    return EvalDataset(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        cases=[EvalCase(**c) if isinstance(c, dict) else EvalCase(message=str(c)) for c in cases_data],
    )


def _load_yaml(path: Path) -> EvalDataset:
    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML is required for YAML datasets. Install with: pip install pyyaml")
    data = yaml.safe_load(path.read_text())
    cases_data = data if isinstance(data, list) else data.get("cases", data.get("tests", []))
    meta = {} if isinstance(data, list) else {k: v for k, v in data.items() if k not in ("cases", "tests")}
    return EvalDataset(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        cases=[EvalCase(**c) if isinstance(c, dict) else EvalCase(message=str(c)) for c in cases_data],
    )
