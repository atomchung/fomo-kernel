# Test-drive flow

Use when the user has no data and wants to see the product experience.

`prepare --test-drive` uses repository mock data. The Review Plan must have `route=test_drive` and `persist:false`. The session lives in an isolated root: pass `--root <review_plan.state_root>` to every later `preview`, `finalize`, and `resume` call. Run the complete required-question, preview, and one-rule lifecycle so the test drive demonstrates the real workflow rather than a static sample.

Label every conversation and card clearly as demo data. Do not read from or project into the user's production `~/.trade-coach` state, and never mix demo theses into production memory.

Return the private demo card following `references/card-delivery.md`. Return the public demo card only when the user asks for a shareable version.
