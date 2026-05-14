"""File-based prompt store with versioning and A/B testing.

Prompts are stored as YAML files with metadata:

.. code-block:: yaml

    name: assistant
    version: 2
    description: A helpful assistant prompt
    template: |
      You are a helpful assistant named {{ name }}.
      {% if verbose %}Be detailed in your responses.{% endif %}
    variables:
      name:
        type: string
        default: "Yomai"
      verbose:
        type: boolean
        default: false

Version history is tracked via file naming: ``prompts/assistant.v1.yaml``, ``assistant.v2.yaml``.
The latest unversioned file (``assistant.yaml``) is used by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yomai.prompts.template import PromptTemplate


class PromptSpec:
    def __init__(
        self,
        name: str,
        template: str,
        version: int = 1,
        description: str = "",
        variables: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.template = PromptTemplate(template)
        self.version = version
        self.description = description
        self.variables = variables or {}

    @classmethod
    def from_yaml(cls, path: Path) -> PromptSpec:
        try:
            import yaml
        except ImportError as err:
            raise ImportError("PyYAML is required for prompt files. Install with: pip install pyyaml") from err
        data = yaml.safe_load(path.read_text())
        return cls(
            name=data.get("name", path.stem.split(".")[0]),
            template=data["template"],
            version=int(data.get("version", 1)),
            description=data.get("description", ""),
            variables=data.get("variables", {}),
        )

    @classmethod
    def from_json(cls, path: Path) -> PromptSpec:
        import json

        data = json.loads(path.read_text())
        return cls(
            name=data.get("name", path.stem.split(".")[0]),
            template=data["template"],
            version=int(data.get("version", 1)),
            description=data.get("description", ""),
            variables=data.get("variables", {}),
        )

    def render(self, **kwargs: Any) -> str:
        defaults = {k: v.get("default", "") for k, v in self.variables.items()}
        merged = {**defaults, **kwargs}
        return self.template.render(**merged)


class PromptStore:
    def __init__(self, directory: str | Path = "prompts") -> None:
        self.directory = Path(directory)
        self._specs: dict[str, PromptSpec] = {}

    def load_all(self) -> dict[str, PromptSpec]:
        if not self.directory.exists():
            return {}
        for yaml_file in sorted(self.directory.glob("*.yaml")):
            self._load_file(yaml_file)
        for yml_file in sorted(self.directory.glob("*.yml")):
            self._load_file(yml_file)
        for json_file in sorted(self.directory.glob("*.json")):
            self._load_file(json_file)
        return self._specs

    def _load_file(self, path: Path) -> None:
        try:
            if path.suffix in (".yaml", ".yml"):
                spec = PromptSpec.from_yaml(path)
            elif path.suffix == ".json":
                spec = PromptSpec.from_json(path)
            else:
                return
        except Exception:
            return
        name = spec.name
        if name not in self._specs or spec.version >= self._specs[name].version:
            self._specs[name] = spec

    def get(self, name: str) -> PromptSpec | None:
        if not self._specs:
            self.load_all()
        return self._specs.get(name)

    def list_specs(self) -> list[dict[str, Any]]:
        if not self._specs:
            self.load_all()
        return [
            {"name": s.name, "version": s.version, "description": s.description, "variables": list(s.variables.keys())}
            for s in self._specs.values()
        ]
