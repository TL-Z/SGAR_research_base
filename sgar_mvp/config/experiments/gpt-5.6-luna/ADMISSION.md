# GPT-5.6 Luna control-backbone experiment

Status: **experimental conditional pass**, not production default.

- Five control roles passed native strict-schema probes.
- G2 run 1 failed because the generated schema invented `status="success"`.
- G2 run 2 completed successfully; one Profiler request received 429 and passed
  on the single allowed retry.
- The current canonical Luna pricing manifest predates the supplier quote shown
  on 2026-09-17. Do not use this experiment for price-comparison claims until
  catalog/readiness/index identities are regenerated from the updated quote.

Production `sgar_mvp/config.json` remains on GPT-5.6 Sol.
