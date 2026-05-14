"""Tests for the prompt management system: templates, store, CLI."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


class TestPromptTemplate:
    """Prompt template rendering."""

    def test_simple_render(self) -> None:
        from yomai.prompts.template import PromptTemplate

        tmpl = PromptTemplate("Hello, {{ name }}!")
        assert tmpl.render(name="World") == "Hello, World!"

    def test_render_no_vars(self) -> None:
        from yomai.prompts.template import PromptTemplate

        tmpl = PromptTemplate("Hello there")
        assert tmpl.render() == "Hello there"

    def test_conditional_true(self) -> None:
        from yomai.prompts.template import PromptTemplate

        tmpl = PromptTemplate("Start. {% if verbose %}Details here.{% endif %} End.")
        assert tmpl.render(verbose=True) == "Start. Details here. End."

    def test_conditional_false(self) -> None:
        from yomai.prompts.template import PromptTemplate

        tmpl = PromptTemplate("Start. {% if verbose %}Details here.{% endif %} End.")
        assert tmpl.render(verbose=False) == "Start.  End."

    def test_conditional_missing_var(self) -> None:
        from yomai.prompts.template import PromptTemplate

        tmpl = PromptTemplate("X {% if unknown %}Y{% endif %} Z")
        assert tmpl.render() == "X  Z"

    def test_from_file(self) -> None:
        from yomai.prompts.template import PromptTemplate

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello {{ name }}")
            f.flush()
            tmpl = PromptTemplate.from_file(f.name)
            Path(f.name).unlink()
        assert tmpl.render(name="World") == "Hello World"


class TestPromptSpec:
    """Prompt specification from YAML/JSON files."""

    def test_from_yaml(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml not installed")

        from yomai.prompts.store import PromptSpec

        content = """name: assistant
version: 2
description: A helpful assistant
template: |
  You are {{ name }}. {% if verbose %}Be detailed.{% endif %}
variables:
  name:
    type: string
    default: "Bot"
  verbose:
    type: boolean
    default: false
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            spec = PromptSpec.from_yaml(Path(f.name))
            Path(f.name).unlink()

        assert spec.name == "assistant"
        assert spec.version == 2
        assert spec.description == "A helpful assistant"
        assert "name" in spec.variables
        assert "verbose" in spec.variables

    def test_render_with_defaults(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml not installed")

        from yomai.prompts.store import PromptSpec

        spec = PromptSpec(
            name="test",
            template="Hello {{ name }}",
            variables={"name": {"type": "string", "default": "World"}},
        )
        assert spec.render() == "Hello World"
        assert spec.render(name="Yomai") == "Hello Yomai"


class TestPromptStore:
    """Prompt store loading and retrieval."""

    def test_load_all_from_directory(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml not installed")

        from yomai.prompts.store import PromptStore

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "greeting.yaml").write_text("name: greeting\nversion: 1\ntemplate: Hello\nvariables: {}\n")
            (Path(tmpdir) / "farewell.yaml").write_text("name: farewell\nversion: 1\ntemplate: Bye\nvariables: {}\n")

            store = PromptStore(tmpdir)
            specs = store.load_all()
            assert len(specs) >= 2
            assert "greeting" in specs
            assert "farewell" in specs

    def test_get_latest_version(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml not installed")

        from yomai.prompts.store import PromptStore

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "assistant.yaml").write_text(
                "name: assistant\nversion: 2\ntemplate: v2 content\nvariables: {}\n"
            )
            (Path(tmpdir) / "assistant.v1.yaml").write_text(
                "name: assistant\nversion: 1\ntemplate: v1 content\nvariables: {}\n"
            )

            store = PromptStore(tmpdir)
            spec = store.get("assistant")
            assert spec is not None
            assert spec.version == 2

    def test_list_specs(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml not installed")

        from yomai.prompts.store import PromptStore

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.yaml").write_text(
                "name: test\nversion: 1\ntemplate: x\nvariables:\n  var1:\n    type: string\n    default: 'x'\n"
            )
            store = PromptStore(tmpdir)
            specs = store.list_specs()
            assert len(specs) == 1
            assert specs[0]["name"] == "test"
            assert "var1" in specs[0]["variables"]
