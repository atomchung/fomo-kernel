# Book refresh flow

Use whenever a newer holdings view arrives against a book that already exists — "here's my portfolio now", "update my holdings", "I sold some things since last time", and equally "here's my portfolio now, how am I doing?". This is not a side lane the user has to ask for by name. Recording new facts and discussing them are two jobs, and this is the first one: when the new view holds anything this flow would have to ask about, `prepare --route snapshot_review` refuses and names `refresh`, because only this flow asks what happened to a position that disappeared. If the user also wants a review, run it afterwards on the book this flow leaves behind.

It is still not part of the review lifecycle: it produces no card, consumes no review question budget, creates no session, and never touches theses, rules, or problem tracking. Review and `consider` read whatever book this flow leaves behind.

Two routes, and the difference is whether a book already exists:

- **No recorded book yet** — that is onboarding. Use `flows/snapshot-review.md`; the first declaration opens a real review card, and this flow refuses it and says so. It has no prior book, so nothing can have disappeared from it.
- **A recorded book already exists** — this flow, first.

## Step 0 — get the holdings into the envelope

Same as `flows/snapshot-review.md`: transcribe the position table or screenshot locally into the normalized JSON envelope in `references/data-contract.md`, copying only broker-declared facts, and keep the temporary file outside the repository (`/tmp/fomo-kernel-positions.json`). Do not calculate weights, cycle IDs, or anything else. The engine has no OCR or cloud-upload path.

## Step 1 — ask the engine what differs

```bash
python3 engine/review.py refresh \
  --snapshot-json /tmp/fomo-kernel-positions.json
```

This writes nothing. It returns a receipt (`schemas/book-refresh.schema.json`): the frozen `diff`, a `summary`, and `pending_confirmations`.

Narrate the difference from the receipt's own facts. Each diff row states only a derived value and a declared value. **Never suggest why they differ** — a missing trade, a transfer, a split, a fee, and a transcription error look identical at this layer, and the engine refuses to guess between them for the same reason you should. `summary.valuation_coverage.unavailable` names any holding the engine could not value; if it is non-empty, say so rather than presenting the weights as covering the whole book.

If `pending_confirmations` is empty, skip to step 3 with an empty answers list. A `ready` status does not mean nothing changed — appearances, small changes, and cash differences are adopted and disclosed without ceremony, because an additive change removes nothing. Say what is being adopted; do not manufacture a question to accompany it.

## Step 2 — one question, covering everything

Every raised item goes into **one** question. Not one question per ticker: eight disappearances must not turn a two-minute maintenance step into an interrogation. List them together and let one answer classify them.

The engine supplies the facts and the legal answers; the wording is yours. Each item carries `options` — offer exactly those, in the user's own language, and never one that is absent:

- `kind: "disappearance"` → `sold` / `not_captured` / `resupply`. Ask it as what it is: these tickers are in the record and not in this view — did you sell them, or are they just not in this screenshot? Do not lead. Both answers are ordinary, and one of them is destructive.
- `kind: "large_change"` → `confirmed` / `resupply`. State the two share counts and ask whether the new one is right or the view is wrong.

`resupply` on any item means the supplied view itself is wrong. It aborts the whole refresh with nothing written, and the next step is a better screenshot, not a partial adoption.

If the user answers only some items, ask again for the rest before step 3. Finalize refuses a partial answer set by design; do not work around it by dropping the unanswered ones.

## Step 3 — adopt

```bash
python3 engine/review.py refresh \
  --snapshot-json /tmp/fomo-kernel-positions.json \
  --answers '{"refresh_id": "refresh-...", "answers": [{"ticker": "ACME", "classification": "sold"}]}'
```

Pass back the `refresh_id` from step 1 verbatim. The engine recomputes step 1 from scratch under a lock and refuses if the recorded book moved in between; that refusal is not an error to route around — rerun step 1 and show the user the current difference, because the plan they answered no longer describes their book.

Report what was recorded, in the user's terms:

- a `sold` position left the book. Its exit is now tracked like any other, and it will come back at the 30/60/90 checkpoints. Say plainly that **no sale price was recorded**, so it does not count toward win rate, payoff, or exit discipline — supplying the actual trade later is the way to make it count. (It does not upgrade automatically today; see #519.)
- a `not_captured` position stayed, carried forward from the record. If the same position is missing again next time, this flow will ask again — there is no answer memory. Saying so once is honest; nagging about it is not.
- `reconciliation: "reconciled"` means the adopted book matched the record exactly, so nothing was rewritten. That is a clean result, not a failure.

## How this flow relates to the shared runtime contracts

There is no card in this flow, so the card delivery contract has nothing to route. `references/interaction-delivery.md` is scoped to full-tier reviews and its presentation-evidence machinery (`references/ux-receipt.md`) does not apply here either — there is no card to prove was shown, and no session to attach a receipt to.

What still applies is the part that is about the human: step 2 is a real question with real consequences, so it gets its own visible turn and a real answer before step 3 runs. Do not bundle it into a status update, and do not answer it on the user's behalf from context.

## Boundaries

- Never invent a sale price, date, or quantity for a confirmed disappearance. The engine's absence record structurally has no field for one.
- Never present a position's cost basis as what it sold for.
- Do not run `prepare`, `preview`, `finalize`, or `capture` as part of this flow. A review afterwards is its own turn, against the book this flow just updated — report what was recorded first, then start it.
- Do not edit `ledger.jsonl` or any artifact by hand to "fix" a difference.
