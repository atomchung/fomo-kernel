# fomo-kernel Codex UI probe

This is the bounded implementation for issue #261. It is a local MCP Apps SDK
probe for two facts that a Markdown-only conversation cannot prove:

- a visual review-card surface can appear in the host;
- a user click can call a tool and return one canonical option value.

It is deliberately **demo-only**. The two tools expose fixed synthetic copy in
`zh-TW` or `en`; they never read `~/.trade-coach`, a fomo-kernel session, a
brokerage file, `answers.json`, or a private card artifact. It therefore does
not modify the production review workflow or make a privacy claim for private
data in a Codex widget.

## Local run

```bash
npm install
npm test
npm start
```

`npm start` exposes a local MCP endpoint at `http://localhost:8787/mcp` for an
MCP inspector. The plugin's `.mcp.json` uses `node ./server.js --stdio`, so a
Codex plugin host can launch the same server over stdio.

## Dogfood procedure

1. Install this local plugin through Codex's plugin flow.
2. Invoke `fomo_show_demo_card` with `locale: "zh-TW"`; verify the styled card
   is visible rather than a file link or a Markdown paraphrase.
3. Invoke `fomo_show_demo_question` with `locale: "zh-TW"`; click either
   option and verify the tool result contains `rule_a` or `rule_b`.
4. Record the actual mode and the owner's controls/card verdict with
   `skills/fomo-kernel/tools/ux_receipt.py`; only then decide whether a private
   artifact handoff is safe to design.

## Non-goals and next gate

This probe does not replace `engine/review.py`, record an answer, render an
engine private card, or bypass `interaction-delivery.md`. Adding a path-based
handoff for engine HTML is intentionally deferred: it must first prove that the
Codex host's data boundary, local-path containment, locale propagation, widget
failure fallback, and owner verdict are acceptable for private review data.
