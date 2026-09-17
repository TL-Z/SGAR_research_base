# Backbone Admission — 2026-09-17

## Scope

Benchmark-independent five-role strict-schema admission followed by the same G2
Compiler/E2E case. Each role or E2E candidate was allowed at most one retry.
The production `gpt-5.6-sol` policy, default config, effective pool, readiness,
and active index were not replaced.

## Control-role matrix

The integrated suite made 46 physical sends, received 46 responses, and cost
USD `0.222943358000`. Full evidence is in `CONTROL_ROLE_MATRIX.json`.

| Candidate | Exact gateway ID | Five-role result | Retry observations |
|---|---|---|---|
| GPT-5.6 Terra High | `gpt-5.6-terra` | 5/5 passed | all first attempt |
| GPT-5.6 Luna High | `gpt-5.6-luna` | 5/5 passed | all first attempt |
| DeepSeek V4.1 Flash High | `deepseek-v4.1-flash` | 5/5 passed | all first attempt |
| Claude Haiku 4.5 High | `claude-haiku-4-5-20251001` | failed | Compiler and Adaptation failed twice; Evaluator failed then passed |
| Claude Haiku 4.5 Thinking High | `claude-haiku-4-5-20251001-thinking` | failed | Compiler, Adaptation, Evaluator failed twice |
| Claude Sonnet 5 High | `claude-sonnet-5` | 5/5 passed in integrated suite | earlier independent Evaluator probe refused; inconsistent across two trials |
| Gemini 3.8 Flash High | `gemini-3.8-flash` | 5/5 passed | all first attempt |
| GPT-5.6 Sol baseline | `gpt-5.6-sol` | 5/5 passed | all first attempt |

## Compiler/E2E evidence

| Candidate | Run 1 | Run 2 | Admission |
|---|---|---|---|
| Luna High | failed: invented unsupported `status="success"`, correctly rejected by Evaluator | succeeded end-to-end; one Profiler 429 recovered on its allowed retry | conditional pass; 1/2 E2E success |
| Terra High | failed: final Compiler schema-graph cycle, correction declared contract unachievable | failed: Planner/Compiler input declaration was incomplete (`required_input_missing`) | rejected; 0/2 E2E success |
| Gemini 3.8 Flash High | prior real Compiler HTTP 400 | retry again produced HTTP 400 for two Compiler requests | rejected; reproducible transport/schema incompatibility |
| DeepSeek V4.1 Flash High | real Planner timed out after ~1200 s | separate run reached Profiler, which timed out after ~1200 s | rejected; unstable long requests |
| Sonnet 5 High | not advanced to E2E because its two role trials disagreed and the retry allowance was exhausted | n/a | rejected for stability |
| Haiku ordinary/thinking | role admission failed after retries | n/a | rejected before E2E |
| Sol baseline | existing G2-equivalent run succeeded | integrated five-role baseline passed | production baseline retained |

## Costs

Integrated role-suite costs:

- Terra: USD `0.006485940000`
- Luna: USD `0.006526980000`
- DeepSeek: USD `0.001623280000`
- Haiku ordinary: USD `0.070486200000`
- Haiku thinking: USD `0.088800300000`
- Sonnet 5: USD `0.031768038000`
- Gemini: USD `0.002682340000`
- Sol: USD `0.014570280000`

E2E costs recorded by the currently active project pricing catalog:

- Luna run 1: USD `0.065314080000`
- Luna run 2: USD `0.126495486000`
- Terra run 1: USD `0.236035080000`
- Terra run 2: USD `0.083096712000`
- Gemini retry: USD `0.016680432000`

The supplier screenshot changes Luna pricing relative to the current production
manifest. Therefore the Luna E2E totals above are internally recorded costs, not
a claim that the production price catalog is current.

## Evidence paths

- Integrated role matrix: `maintenance_outputs/backbone-admission-20260917/CONTROL_ROLE_MATRIX.json`
- Luna run 1: `/ssd/zhoutianle/runtime/sgar/runs/luna-live-g2-admission-1-20260917/`
- Luna run 2: `/ssd/zhoutianle/runtime/sgar/runs/luna-live-g2-admission-2-20260917/`
- Terra run 1: `/ssd/zhoutianle/runtime/sgar/runs/terra-live-g2-admission-1-20260917/`
- Terra run 2: `/ssd/zhoutianle/runtime/sgar/runs/terra-live-g2-admission-2-20260917/`
- Gemini retry: `/ssd/zhoutianle/runtime/sgar/runs/gemini-live-g2-admission-retry-20260917/`
- DeepSeek terminal run: `/ssd/zhoutianle/runtime/sgar/runs/deepseek-live-g2-validation-final-20260916/`
- DeepSeek partial run: `/ssd/zhoutianle/runtime/sgar/runs/deepseek-live-g2-validation-rerun-20260916/`

## Decision

1. Keep `gpt-5.6-sol` as production default.
2. Luna is the only new candidate that completed G2, but its 1/2 success rate and
   one recovered 429 make it an experimental fallback, not a production replacement.
3. Do not add Terra, Haiku, Gemini, DeepSeek, or Sonnet as the unified five-role
   backbone from this evidence.
4. Before enabling the Luna experiment for regular use, update its supplier quote
   in the canonical model source and regenerate catalog/readiness/index identities.

No response cleaning, prompt changes, schema weakening, model-specific runtime
branch, or hidden failover was used.
