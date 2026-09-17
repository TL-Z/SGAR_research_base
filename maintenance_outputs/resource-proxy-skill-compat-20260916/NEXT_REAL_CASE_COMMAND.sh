#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/zhoutianle/Projects/SGAR_research_base
PYTHON=/ssd/zhoutianle/envs/sgar/bin/python
REQUEST_MANIFEST=${REQUEST_MANIFEST:?set REQUEST_MANIFEST to a public request manifest}
PUBLIC_INPUT_ROOT=${PUBLIC_INPUT_ROOT:?set PUBLIC_INPUT_ROOT to an authorized public input root}
test -n "${LLM_API_KEY:-}" || { echo 'LLM_API_KEY is required' >&2; exit 2; }
test -n "${SGAR_EMBEDDING_API_KEY:-}" || { echo 'SGAR_EMBEDDING_API_KEY is required' >&2; exit 2; }
cd "$ROOT"
exec "$PYTHON" sgar_mvp/main.py \
  --request-manifest "$REQUEST_MANIFEST" \
  --public-input-root "$PUBLIC_INPUT_ROOT" \
  --runtime-authority git \
  --planner-variant resource_aware
