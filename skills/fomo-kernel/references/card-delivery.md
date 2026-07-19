# Card delivery

How an agent shows an engine-rendered review card to the user. This contract applies at both card moments: the preview shown at the rule-choice step and the final card after finalize.

Record the actual user-visible mode following `interaction-delivery.md`. Artifact creation and a file path are not evidence that the card appeared inline.

## Never re-render by hand

- Card artifacts are engine-rendered. Never re-summarize, paraphrase, or partially quote the card body in chat: retyping the card risks silently dropping a line the engine computed (for example an alpha/beta figure). Never hand-assemble card HTML and never invent or restate values.
- The canonical card text is the Markdown artifact: `card-private-preview.md` at preview, `sessions/<session_id>/card-private.md` after finalize. The styled artifact is the matching engine-rendered `.html` file (preview emits its path as `private_card_html_path`); both are rendered from the same structured content.

## Choose a channel per surface

- Graphical surface (claude.ai, desktop app, IDE webview): render a widget from the engine HTML artifact. Use the fragment between the `<!-- WIDGET-FRAGMENT-START -->` and `<!-- WIDGET-FRAGMENT-END -->` markers — a self-contained `<style>` plus `<div class="rc">` block built for pasting into a widget host. If no widget tool exists or rendering fails, fall back to pasting the canonical Markdown card text verbatim.
- Terminal or plain-text surface: paste the canonical Markdown card text verbatim, and mention that the `.html` artifact can be opened in a browser for the styled card.

## Try the widget once per session

Whether widget rendering works is an execution-layer fact the engine cannot detect. On a graphical surface, attempt the widget once per session instead of assuming plain text; degrade to verbatim Markdown only after that attempt fails.
