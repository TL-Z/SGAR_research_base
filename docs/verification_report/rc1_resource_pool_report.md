# SGAR Resource Pool RC1 Readiness Report

- Generated at: `2026-09-15T18:18:22.525079+00:00`
- Catalog total: **338**
- Effective total: **328**
- Catalog SHA256: `sha256:45270383d2c41f14881712f4e8aa77c7c900380ad8cf3912624bc4d5261249f4`
- Docker image ID: `sha256:3df4a3e71cdb166638b00b2b1af650d54068abb07c11c05042d3f67c9d699185`

## Readiness counts

| Type and status | Count |
| --- | ---: |
| `Agent:ready` | 20 |
| `Model:blocked` | 2 |
| `Model:inactive` | 1 |
| `Model:ready` | 16 |
| `Skill:ready` | 154 |
| `Tool:inactive` | 2 |
| `Tool:ready` | 138 |
| `Tool:transient_failure` | 5 |

## Excluded resources

| Resource | Type | Status | Reason |
| --- | --- | --- | --- |
| `model.claude_fable_5_1.v1` | Model | inactive | control_model_outside_candidate_pool |
| `model.claude_opus_5.v1` | Model | blocked | native_strict_schema_not_live_verified |
| `model.qwen3_coder_next.v1` | Model | blocked | native_strict_schema_not_live_verified |
| `tool.api.arxiv.search.v1` | Tool | transient_failure | network_or_provider_transient |
| `tool.api.coingecko.price.v1` | Tool | transient_failure | network_or_provider_transient |
| `tool.api.google_dns.v1` | Tool | transient_failure | network_or_provider_transient |
| `tool.api.nominatim.geocode.v1` | Tool | transient_failure | network_or_provider_transient |
| `tool.api.wikipedia.summary.v1` | Tool | transient_failure | network_or_provider_transient |
| `tool.eslint_code_formatter.v1` | Tool | inactive | catalog_inactive |
| `tool.npm_package_auditor.v1` | Tool | inactive | catalog_inactive |
