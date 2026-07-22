# Card delivery

How an agent shows an engine-rendered review card to the user. This contract applies at both card moments: the preview shown at the rule-choice step and the final card after finalize.

Record the actual user-visible mode following `interaction-delivery.md`. Artifact creation and a file path are not evidence that the card appeared inline.

## Never re-render by hand

- Card artifacts are engine-rendered. Never re-summarize, paraphrase, or partially quote the card body in chat: retyping the card risks silently dropping a line the engine computed (for example an alpha/beta figure). Never hand-assemble card HTML and never invent or restate values.
- The canonical card text is the Markdown artifact: `card-private-preview.md` at preview, `sessions/<session_id>/card-private.md` after finalize. The styled artifact is the matching engine-rendered `.html` file (preview emits its path as `private_card_html_path`); both are rendered from the same structured content.

## Choose a channel per surface

- Graphical surface (claude.ai, desktop app, IDE webview): render a widget from the engine HTML artifact. Use the fragment between the `<!-- WIDGET-FRAGMENT-START -->` and `<!-- WIDGET-FRAGMENT-END -->` markers — a self-contained `<style>` plus `<div class="rc">` block built for pasting into a widget host. If no widget tool exists or rendering fails, fall back to pasting the canonical Markdown card text verbatim.
- Conversation-only, terminal, or plain-text surface: paste the canonical Markdown card text verbatim and **do not put it in a code fence**. Its title plus the engine-rendered `Key risk` / `Next rule` blockquote is the readable last-resort layout; the four detailed blocks remain immediately below it. Immediately after that complete card, offer the matching local HTML artifact as an additional, clickable file link when the host supports local-file links (for example, `[Open the full HTML card](<absolute-html-path>)`); if local links are unavailable, show the exact local path instead. The HTML artifact is optional and may be offered as a browser view, never as a replacement for the inline text.

The terminal/CLI fallback is the same canonical Markdown artifact, not a separately authored report. After finalization, `python3 engine/review.py render --root <root> --session-id <id> --format private-markdown` writes that exact private card directly to stdout. A terminal that renders Markdown preserves the full hierarchy; a raw terminal still keeps the heading, blockquote, panel marks, and fixed block order legible. Do not make a second CLI template or calculate a condensed summary outside `card_renderer.py`.

For the `plain_text` adapter — the universal fallback for unknown, plugin-absent, or not-yet-validated hosts (for example, Codex without a proven AppBridge bridge) — use the localized equivalent of this fixed
post-card line after each preview and final card:

```text
Complete styled version: [Open the full HTML card](<absolute-html-path>)
```

The link target must be the engine-emitted private HTML path for that same
stage. Never substitute a public card, a hand-authored HTML file, or a
directory link.

## Try the widget once per session

Whether widget rendering works is an execution-layer fact the engine cannot detect. On a graphical surface, attempt the widget once per session instead of assuming plain text; degrade to verbatim Markdown only after that attempt fails.
