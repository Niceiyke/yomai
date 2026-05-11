#!/usr/bin/env bash
set -euo pipefail

TMP=${TMPDIR:-/tmp}/yomai-install-smoke-$$
mkdir -p "$TMP"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

uv build >/dev/null
uv venv "$TMP/.venv" >/dev/null
uv pip install --python "$TMP/.venv/bin/python" dist/yomai-0.1.0-py3-none-any.whl >/dev/null
"$TMP/.venv/bin/python" - <<'PY'
from yomai import Yomai, tool
from yomai.config import Config, LLMConfig
from yomai.memory import MemoryBackend
from yomai.workflow import WorkflowRunner
from yomai.llm import LLMProvider
from yomai.testing import YomaiTestClient, mock_llm, capture_tools
print("install smoke ok")
PY
