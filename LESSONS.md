# Lessons

One line per miss: date, the class of miss, the mechanism that now catches it.

- 2026-09-16 (offline gate) A subprocess test dropped `BIO3D_DATA_DIR` to sidestep a DB-path side effect and thereby pointed the seed at the checkout's own `data/assets`; the pollution was invisible to a single run (later tests only *skipped* less) and surfaced as five failures the moment the suite ran twice in one checkout. Now: the seed subprocess always gets an isolated data dir, a test checks the checkout's `data/assets` by mtime after a seed run, and the guardrails `test` job runs the suite twice (second time offline), so first-run state that changes second-run behaviour is a red check.
