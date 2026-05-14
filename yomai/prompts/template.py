"""Jinja2-style prompt template rendering with variable interpolation."""

from __future__ import annotations

import re
from typing import Any


class PromptTemplate:
    def __init__(self, template: str) -> None:
        self.template = template

    def render(self, **variables: Any) -> str:
        result = self.template
        for key, value in variables.items():
            result = result.replace(f"{{{{ {key} }}}}", str(value))
            result = result.replace(f"{{{{{key}}}}}", str(value))

        result = self._render_conditionals(result, variables)
        return result

    def _render_conditionals(self, text: str, variables: dict[str, Any]) -> str:
        pattern = re.compile(r"\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*endif\s*%\}", re.DOTALL)

        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            body = match.group(2)
            value = variables.get(var_name, False)
            if value:
                inner_pattern = re.compile(r"\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*endif\s*%\}", re.DOTALL)
                return inner_pattern.sub(replacer, body)
            else:
                return ""

        return pattern.sub(replacer, text)

    @classmethod
    def from_file(cls, path: str) -> PromptTemplate:
        with open(path) as f:
            return cls(f.read())
