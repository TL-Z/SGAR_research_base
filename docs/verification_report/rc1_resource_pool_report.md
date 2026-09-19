# SGAR Resource Pool RC1 Readiness Report

- Generated at: `2026-09-18T19:36:03.187428+00:00`
- Catalog total: **338**
- Effective total: **328**
- Catalog SHA256: `sha256:7b5965be0036b9bd281c8a67872e6357b869cbedbb20af6124f525b8073c1333`
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
