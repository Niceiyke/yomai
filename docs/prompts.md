# Prompt Management

Manage prompt templates with variable interpolation, conditionals, and file-based versioning.

## Quick Start

Create a prompt file (`prompts/assistant.yaml`):

```yaml
name: assistant
version: 1
description: A helpful assistant with configurable verbosity
template: |
  You are a helpful assistant named {{ name }}.
  {% if verbose %}Be thorough and detailed in your responses.{% endif %}
  {% if formal %}Use formal language.{% endif %}
variables:
  name:
    type: string
    default: "Yomai"
  verbose:
    type: boolean
    default: false
  formal:
    type: boolean
    default: false
```

Use it in your agent:

```python
@app.agent("/chat", prompt="assistant")
async def chat(message: str, session_id: str):
    pass
```

Or inline:

```python
@app.agent("/chat", prompt="You are {{ role }}. Be helpful.")
async def chat(message: str, session_id: str):
    pass
```

## Template Syntax

Uses a Jinja2-inspired syntax:

- `{{ variable }}` — Variable interpolation
- `{% if variable %}...{% endif %}` — Conditional blocks

```yaml
template: |
  You are {{ role }}.
  {% if tools_available %}You have access to these tools: {{ tool_list }}{% endif %}
  {% if expert_mode %}You are an expert in the domain.{% endif %}
```

Variables default to their `default` value from the YAML spec. Override at render time via the `render()` method.

## CLI Commands

```bash
# List all prompts
yomai prompt list

# Create a new prompt
yomai prompt create --name assistant --template "You are a helpful assistant."

# Validate a prompt file
yomai prompt validate --file prompts/assistant.yaml

# Render a prompt with variables
yomai prompt render --name assistant --vars '{"name":"Helper","verbose":true}'
```

## Versioning

Version prompts by appending `.v{N}` to the filename:

```
prompts/
  assistant.v1.yaml    # Initial version
  assistant.v2.yaml    # Updated version
  assistant.yaml       # Latest (used by default)
```

The prompt store loads the highest version number available.

## Programmatic Use

```python
from yomai.prompts import PromptTemplate, PromptStore

# Quick render
tmpl = PromptTemplate("Hello {{ name }}")
print(tmpl.render(name="World"))  # "Hello World"

# Load from store
store = PromptStore("prompts")
spec = store.get("assistant")
print(spec.render(name="Bot", verbose=True))

# List available
for s in store.list_specs():
    print(f"{s['name']} v{s['version']} — vars: {s['variables']}")
```

## A/B Testing

To run A/B tests, create two prompt variants and switch between them:

```python
import random

@app.agent("/chat", prompt="assistant_variant_a" if random.random() < 0.5 else "assistant_variant_b")
async def chat(message: str, session_id: str):
    pass
```

Track which variant each session used via hooks.
