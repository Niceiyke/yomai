# LLM Providers

Yomai supports multiple LLM providers through a unified streaming interface.

## Available Providers

| Provider | Config Key | Default Model | SDK Required |
|---|---|---|---|
| Anthropic | `anthropic` | `claude-sonnet-4-20250514` | `anthropic` (built-in) |
| OpenAI | `openai` | `gpt-4o-mini` | `openai` (built-in) |
| Ollama | `ollama` | `llama3.2` | None (OpenAI-compatible) |
| Google Gemini | `gemini` | `gemini-2.0-flash` | `pip install google-genai` |
| Mistral AI | `mistral` | `mistral-large-latest` | `pip install mistralai` |
| Groq | `groq` | `llama-3.3-70b-versatile` | `pip install openai` (shared) |
| vLLM | `vllm` | `meta-llama/Meta-Llama-3-8B-Instruct` | `pip install openai` (shared) |

## Configuration

```python
from yomai import Yomai
from yomai.config import LLMConfig

# Anthropic (default)
app = Yomai(llm=LLMConfig(provider="anthropic", api_key="sk-ant-..."))

# OpenAI
app = Yomai(llm=LLMConfig(provider="openai", api_key="sk-..."))

# Ollama (local)
app = Yomai(llm=LLMConfig(provider="ollama", base_url="http://localhost:11434/v1"))

# Gemini
app = Yomai(llm=LLMConfig(provider="gemini", api_key="..."))

# Mistral
app = Yomai(llm=LLMConfig(provider="mistral", api_key="..."))

# Groq
app = Yomai(llm=LLMConfig(provider="groq", api_key="gsk_..."))

# vLLM (self-hosted)
app = Yomai(llm=LLMConfig(provider="vllm", base_url="http://localhost:8000/v1"))
```

## Environment Variable Auto-Detection

Each provider checks its standard env var:

| Provider | Env Var |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI / Ollama | `OPENAI_API_KEY` |
| Gemini | `GEMINI_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| Groq | `GROQ_API_KEY` |
| vLLM | `VLLM_API_KEY`, `VLLM_BASE_URL` |

## Installing Provider Dependencies

```bash
# Install with specific provider
pip install yomai[gemini]
pip install yomai[mistral]
pip install yomai[groq]
pip install yomai[vllm]

# Or install all at once
pip install yomai[gemini,mistral,groq,vllm]
```

## Common Config Fields

All providers respect these `LLMConfig` fields:

```python
LLMConfig(
    provider="anthropic",
    model="claude-sonnet-4-20250514",
    api_key="...",
    base_url=None,           # Custom endpoint override
    max_tokens=1024,
    cost_per_token={"input": 0.000003, "output": 0.000015},
    max_retries=3,
    retry_backoff_secs=1.0,
    retry_backoff_multiplier=2.0,
    strip_reasoning=False,   # Strip <think> tags from output
)
```
