# Test-drive flow

Use when the user has no data and wants to see the product experience.

`prepare --test-drive` uses repository mock data. The Review Plan must have `route=test_drive` and `persist:false`. The session lives in an isolated root: pass `--root <review_plan.state_root>` to every later `preview`, `finalize`, and `resume` call. Run the complete required-question, preview, and one-rule lifecycle so the test drive demonstrates the real workflow rather than a static sample.

Label every conversation and card clearly as demo data. Do not read from or project into the user's production `~/.trade-coach` state, and never mix demo theses into production memory.

Declare capabilities and record user-visible questions and cards following `references/interaction-delivery.md`. With the same isolated `--root`, validate eligible private surfaces through `resume --question-surfaces`, or use the unchanged engine fallback; this does not change the demo queue, answers, or one-rule lifecycle. Return the private demo card inline following `references/card-delivery.md`. Return the public demo card only when the user asks for a shareable version.

The funnel does not end unstated (#760): a demo walk that never says how to move from mock data to the user's own leaves them to reconstruct the next step alone. `copy/*.json`'s `demo_badge` already states this once, unprompted, on every test-drive card's own surface — do not additionally invent a chart, artifact, or multi-tool production to make the same point (`SKILL.md`'s answer shape). If the user asks how to bring real data, name the concrete route: their own broker CSV export, passed to the trade command `SKILL.md`'s routing table already shows (`engine/review.py prepare <CSV...>`) instead of the `--test-drive` flag.
