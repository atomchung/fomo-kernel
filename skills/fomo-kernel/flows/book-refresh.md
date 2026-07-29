# Book refresh flow

Use whenever a newer holdings view arrives against a book that already exists — "here's my portfolio now", "update my holdings", "I sold some things since last time", and equally "here's my portfolio now, how am I doing?". This is not a side lane the user has to ask for by name. Recording new facts and discussing them are two jobs, and this is the first one: when the new view holds anything this flow would have to ask about, `prepare --route snapshot_review` refuses and names `refresh`, because only this flow asks what happened to a position that disappeared. If the user also wants a review, run it afterwards on the book this flow leaves behind.

It is still not part of the review lifecycle: it produces no card, consumes no review question budget, creates no session, and never touches theses, rules, or problem tracking. Review and `consider` read whatever book this flow leaves behind.

Two routes, and the difference is whether a book already exists:

- **No recorded book yet** — that is onboarding. Use `flows/snapshot-review.md`; the first declaration opens a real review card, and this flow refuses it and says so. Nothing has been recorded, so nothing can have disappeared from it. This means a root with no history at all: a user who onboarded with a transaction export has a recorded book, because every source records the book at its own time (#549), so they belong in the route below.
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

If `pending_confirmations` is empty, skip to step 3 with an empty answers list. A `ready` status does not mean nothing changed — small changes, cash differences, and market or currency differences are adopted and disclosed without ceremony. Say what is being adopted; do not manufacture a question to accompany it.

## Step 2 — one question, covering everything

Every raised item goes into **one** question. Not one question per ticker: eight disappearances, or eight appearances, must not turn a two-minute maintenance step into an interrogation. List them together and let one answer classify them.

The engine supplies the facts and the legal answers; the wording is yours. Each item carries `options` — offer exactly those, in the user's own language, and never one that is absent:

- `kind: "disappearance"` → `sold` / `not_captured` / `resupply`. Ask it as what it is: these tickers are in the record and not in this view — did you sell them, or are they just not in this screenshot? Do not lead. Both answers are ordinary, and one of them is destructive.
- `kind: "appearance"` → `confirmed` / `resupply`. These tickers are in this view and not in the record, so the engine knows nothing about them beyond today's share count. A `confirmed` answer must also carry `held_months` — roughly how many months they have held it — and, when the item says `needs_avg_cost`, an `avg_cost`. Ask for a rough duration, not a date: nobody remembers the day, and the engine only needs the month. **"I don't know" is an ordinary answer** for either field; pass `null` and say plainly that the position stays in the book but drops out of holding-period readings. Never estimate on the user's behalf, and never convert months into a date yourself — pass the number through and let the engine derive the start.
- `kind: "large_change"` → `confirmed` / `resupply`. State the two share counts and ask whether the new one is right or the view is wrong.

`resupply` on any item means the supplied view itself is wrong. It aborts the whole refresh with nothing written, and the next step is a better screenshot, not a partial adoption.

If the user answers only some items, ask again for the rest before step 3. Finalize refuses a partial answer set by design; do not work around it by dropping the unanswered ones.

## Step 3 — adopt

```bash
python3 engine/review.py refresh \
  --snapshot-json /tmp/fomo-kernel-positions.json \
  --answers '{"refresh_id": "refresh-...", "answers": [
      {"ticker": "ACME", "classification": "sold"},
      {"ticker": "NEWCO", "classification": "confirmed", "held_months": 18, "avg_cost": 41.5}]}'
```

Pass back the `refresh_id` from step 1 verbatim. The engine recomputes step 1 from scratch under a lock and refuses if the recorded book moved in between; that refusal is not an error to route around — rerun step 1 and show the user the current difference, because the plan they answered no longer describes their book.

Report what was recorded, in the user's terms:

- a `sold` position left the book. Its exit is now tracked like any other, and it will come back at the 30/60/90 checkpoints. Say plainly that **no sale price was recorded**, so it does not count toward win rate, payoff, or exit discipline — supplying the actual trade later is the way to make it count. (It does not upgrade automatically today; see #519.)
- a `not_captured` position stayed, carried forward from the record. If the same position is missing again next time, this flow will ask again — there is no answer memory. Saying so once is honest; nagging about it is not.
- a `confirmed` appearance entered the book with the start date the engine derived from the months given. Report it as the approximation it is — "about eighteen months" — never as a specific day; the engine stores it stamped as an estimate for exactly that reason. If they said they did not know, say the position is recorded and simply will not appear in holding-period readings until a real trade record exists. This answer is not asked again: the position is held from now on, not appearing.
- `reconciliation: "reconciled"` means the adopted book matched the record exactly, so nothing was rewritten. That is a clean result, not a failure.

## How this flow relates to the shared runtime contracts

There is no card in this flow, so the card delivery contract has nothing to route: `references/interaction-delivery.md` is scoped to full-tier reviews, and none of its card rules apply here.

The presentation trace is a separate question, and the answer changed with #523. This flow shows the user something real — the difference, and then what was recorded — so `references/ux-receipt.md` carries a card-free `refresh` route whose evidence is exactly that change surface, with the engine's `refresh_id` as the trace's session id. A maintainer QA walkthrough records it; an ordinary refresh needs no receipt, the same way it needs no session.

What still applies is the part that is about the human: step 2 is a real question with real consequences, so it gets its own visible turn and a real answer before step 3 runs. Do not bundle it into a status update, and do not answer it on the user's behalf from context.

## Boundaries

- Never invent a sale price, date, or quantity for a confirmed disappearance. The engine's absence record structurally has no field for one.
- Never invent, guess, or round a holding duration or a cost for a confirmed appearance, and never turn months into a date yourself. Both fields accept `null`, which is what "I don't know" means; an unasked question answered on the user's behalf is the failure this whole step exists to prevent.
- Never present a position's cost basis as what it sold for.
- Do not run `prepare`, `preview`, `finalize`, or `capture` as part of this flow. A review afterwards is its own turn, against the book this flow just updated — report what was recorded first, then start it.
- Do not edit `ledger.jsonl` or any artifact by hand to "fix" a difference.
