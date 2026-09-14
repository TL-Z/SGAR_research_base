# Tool registry (source of truth for the pool)

One JSON manifest per tool, grouped by execution runtime. Edit / add files
here, then run `python Pool/resources/produce/build_pool.py` to regenerate
`combine.json` and `tools.json`, then `python build_index.py` to rebuild the
semantic index.

- `python_script/` tools also have an implementation `.py` in `../` (the
  parent tools/ directory); their manifest `execution.uri` points to it.
- `rest_api/`, `cli/`, `python_library/`, `mcp_server/` are declared tools
  that wrap a recognized external service/binary/library (see `provenance`).
