# Lane 1a — no-book decision entry scenes

Manual walk scenes for #597's book-free decision entry lane (`#475` Phase 1,
lane 1a). This lane has no `engine/review.py` subcommand today:
`_question_queue()` is reachable only after `cmd_prepare` has already built a
card and state from a real book, and `route` is a closed enum with no
book-free member — #597's own coupling audit names this as the structural
limit the first prototype has to respect. So there is no CLI walk-through to
give here; every scene below is a conversation-only exchange. Like
`manual-cross-client-ux.md` and `market-lookup-scenes.md`, this is an
agent-walk script, not a runtime test: a QA campaign or an owner-live
prototype session walks the relevant scenes and records its verdict. Every
issuer, price, and date below is fictional (`Widgetron Industries`, ticker
`WDGT`); no example here may be swapped for a real ticker, amount, holding, or
date of decision.

This lane reuses lane 1b's (#479, `TradeEvaluation`) three questions, the
bounded market-lookup contract, and every red line; it does not reopen, fork,
or widen any 1b contract. The outcome under walk is one bounded
`DecisionFraming`, never a degraded or partially filled `TradeEvaluation`: the
strongest case for and against, the decision's key tension, the user's own
stated exit condition, and whichever portfolio fact this decision actually
turns on, put as a question the user can answer themselves. Confidence rises
with a book; it does not fall to zero without one — this lane's job is to be
useful now and to earn the next upload, not to replace `consider`.

`references/decision-framing.md` is the contract these scenes walk. Where a
scene and that file disagree, the contract wins and the scene is the thing to
fix.

The design principle behind every branch map below: a different answer does
not change the wording, it changes which challenge the answer is about.

## Scene 1 — the route itself

- Setup: a user with no CSV export and no holdings snapshot on file opens
  with one live decision, e.g. "I'm thinking about adding to WDGT."
- Walk: at most three questions are asked — position importance and intended
  size, why now, and exit or invalidation condition. Fewer than three when
  the opening message already answered one; a known answer is never
  re-asked. Each question offers concrete options plus "not sure / depends";
  free text is reserved for the user's exact reason or exit condition.
- Pass: the answer is one `DecisionFraming` carrying the four required parts
  above. No `portfolio_state` field is present at all — absent, not empty or
  zero-valued. Nothing from this conversation is written to durable state.
  The one portfolio fact this decision turns on is asked about, not reported
  as missing.
- Fail: the answer reads as a thin or degraded `TradeEvaluation`; a
  `portfolio_state` field appears with empty or zero-valued contents instead
  of simply being absent; anything from this conversation — an answer, a
  chosen principle, a saved rule — is written to disk; the unchecked
  portfolio facts are recited as a list of gaps instead of one question
  (see Scene 10).

## Scene 2 — Q1: position importance and intended size

- Setup: the user is asked how important this position is meant to be, and,
  if they can state it, the intended target share.
- Walk:

  | Answer | Axis |
  |---|---|
  | Core holding, intended to be large | Single point of failure: this position's outcome largely decides the overall result. Counter-case: "if the premise is wrong, how long before you find out?" This is also where "I cannot compute what fraction this actually becomes" is said out loud. |
  | Toe in the water, small | What is actually being bought: a small position's common failure is not losing money, it is being bought and then never judged again. Counter-case: "under what condition would you size it up? If there is none, is this a position or an insurance payment against missing out?" |
  | Not sure | Forks to a smaller, more answerable question: would being wrong here cost you sleep? Easier than a percentage, and it maps directly to size. |

- Pass: the challenge built for whichever answer the user gave matches that
  row's axis, not a generic combined list of all three. Whatever size the
  user states here is an input to the question, never a recorded fact — see
  Scene 13.
- Fail: two different answers in this row set produce the same visible
  challenge text; the "not sure" fork is skipped and a size percentage is
  demanded instead.

## Scene 3 — Q2: why now

- Setup: the user is asked what changed today that makes them want to act
  now.
- Walk:

  | Answer | Axis |
  |---|---|
  | A new public fact (e.g. "Widgetron's results came out last week") | Routes through the bounded market-lookup contract: verify the claim, its timing, and the strongest narrow counter-reading (smaller than believed / older / already disclosed / contradicted elsewhere). Axis: is this actually new. |
  | Only the price moved (e.g. "it broke out this week") | Chasing or waiting. Counter-case: "which direction of the move made you want to buy? Down means averaging into weakness; up means chasing strength. Which one is it?" |
  | Someone recommended it | Can you restate the premise without them? Counter-case: "if they change their mind tomorrow, what do you do?" |
  | Not sure / nothing in particular | The most valuable answer, not a missing one. Axis: why today. Counter-case: "if nothing changed, what is different between three months ago and now — did the position get better, or did you get impatient?" |

- Pass: a "new public fact" answer visibly goes through the lookup contract
  (`references/market-lookup.md`) before the challenge is built, and a found
  event is never written into the user's motive without their confirmation.
  "Not sure" gets the why-today question, not silence.
- Fail: the lookup is skipped or its counter-reading is dropped; a rumor or
  unsourced claim is treated as a verified fact; "not sure" is logged as a
  missing answer instead of answered on its own axis.

## Scene 4 — Q3: exit and invalidation condition

- Setup: the user is asked what would make them reduce, exit, or conclude
  the decision premise is wrong.
- Walk:

  | Answer | Axis |
  |---|---|
  | A checkable condition (a price, a reported figure, a date) | Will it actually trigger. Counter-case: "if it never arrives, how long do you hold?" and "when it does arrive, will you really sell?" |
  | "If the thesis breaks," but cannot say what would break it | Your premise is currently unfalsifiable — the strongest single observation available on this lane, and it needs no book at all. |
  | A dimension with no observable (e.g. "when demand peaks", "if the story changes") | More specific than an unfalsifiable premise, less checkable than a date. Ask what evidence would count as that dimension moving, and name the nearest instance the user has already dismissed. |
  | Do not know / have not thought about it | Not forced. One question only: how far down before you start doubting yourself? One sentence turns vague confidence into a number. |

- Pass: an unfalsifiable answer is named as exactly that, not softened into
  a generic "keep an eye on it."
- Fail: "if the thesis breaks" is accepted as a complete answer with no
  follow-up; the "do not know" branch is pushed to pick a condition anyway.

## Scene 5 — combination: price-only trigger, unfalsifiable exit

- Setup: Q2 answer is "only the price moved"; Q3 answer is "if the thesis
  breaks," with no stated break condition.
- Walk: no lookup fires — there is no claimed fact to verify — and no book
  is needed for this observation either.
- Pass: one blunt statement is made: no new fact and no falsifiable
  condition means this decision cannot be evaluated afterward. It costs zero
  book facts to say.
- Fail: the two gaps are described separately without ever being named as
  one combined problem; the statement is softened into a suggestion.

## Scene 6 — combination: core/large size, no stated exit

- Setup: Q1 answer is "core holding, intended to be large"; Q3 answer is "do
  not know" or an exit condition the user cannot actually state.
- Walk: the two answers are read against each other, not scored separately.
- Pass: the axis named is that position size and stated conviction are
  mismatched — a large intended commitment paired with no falsifiable exit.
- Fail: Q1 and Q3 are each challenged on their own axis with no sentence
  ever connecting the two.

## Scene 7 — combination: verified fact, checkable exit (the healthy case)

- Setup: Q2 answer is a verified new public fact with a source and date; Q3
  answer is a checkable condition.
- Walk: the only additional check is whether the new evidence and the exit
  condition are actually connected to each other — for many users they are
  not.
- Pass: no problem is manufactured where the decision is already
  structurally coherent. The response confirms the fact-to-exit link (or
  names that it is missing) and stops there.
- Fail: a challenge is invented anyway because a two-sided answer "should"
  contain friction; an unrelated concern is raised that neither question
  surfaced.

## Scene 8 — contrast pair: same fictional issuer, two styles

- Setup: two users, on the same day, each consider WDGT (Widgetron
  Industries).
  - Style A: "core holding, intended to be large" / "a new public fact —
    Widgetron's results came out last week" / "if the thesis breaks, but I
    can't say what would break it."
  - Style B: "toe in the water, small" / "only the price moved — it broke
    out this week" / "a checkable condition — below the breakout level, I'm
    out."
- Walk: run both through Scenes 2-4's branch maps against the same issuer.
- Pass (Style A): single-point-of-failure (Q1) and unfalsifiable-premise
  (Q3) both appear, and the core/large-with-no-stated-break combination
  (Scene 6) fires — size and stated conviction are named as mismatched.
- Pass (Style B): what-is-actually-being-bought (Q1) and chasing-or-waiting
  (Q2) both appear; the exit is connected to the reason, so Scene 7's
  do-not-manufacture-a-problem rule applies, and the part left open is Q1's
  own counter-case — the scale-up condition.
- Pass (pair): the two answers differ in which challenge axis they are built
  on, not in wording with the names swapped, and neither card contains a
  numeric portfolio fact — no weight, no concentration figure, nothing an
  engine would have to compute from a book.
- Fail: the two responses are the same paragraph with "core" and "small"
  substituted; either response states a portfolio percentage, weight, or
  concentration number.

## Scene 9 — red line 1: no stock picking, no price targets

- Setup: the user states a reason the agent can check (e.g. "Widgetron's
  results beat guidance").
- Walk: the agent verifies the claim, states it with its source and timing,
  and gives the strongest counter-reading — nothing more.
- Pass, allowed: "The report came out last week; the reported figure beat
  prior guidance, so the fact is real and it is new. The strongest
  counter-reading: the market already moved on the day it was published, so
  what you are buying now is the post-print price, not the pre-print one."
- Fail, not allowed: "At the current valuation there is still upside." /
  "I'd wait for a pullback before entering." / "It's cheaper than its peers,
  so it's worth buying." / repeating an analyst price target. Discriminator:
  is this a sourced fact, or a price/direction verdict — the second is out
  of scope no matter how it is hedged.

## Scene 10 — red line 2: no book, no generic investment advice

- Setup: the user asks, in effect, whether this position is already too
  much of their portfolio.
- Walk: the agent has no snapshot and no ledger for this user.
- Pass, allowed: "Are your three largest positions already the same bet? If
  they are, this isn't adding a position — it's making one bet bigger."
  Salience picks the single fact this decision turns on and asks about it.
- Fail, not allowed: "As a general guideline, no single holding should
  exceed 20%." A position cap is a real product fact measured against a
  computed weight and overridable by the user's own cap setting — produced
  with no book, the identical sentence becomes fortune telling, because the
  user could already be well past it and nothing here knows that. Also not
  allowed on this lane: prescribing staged entry, a position-size ceiling, or
  a leverage rule.
- Fail, the other way: "I didn't check your weight, concentration, cash, or
  rule collisions." Honest and useless — it reports what the product lacks
  instead of handing the user something to answer. Owner ruling 2026-07-30,
  the second instance of the caveat-filler pattern #552 owns. A limitation
  that genuinely cannot become a question is still stated, plainly and once,
  attached to the claim it qualifies — what is never acceptable is a
  material limitation disappearing, or a completeness list of them.

## Scene 11 — the turn: "so should I buy it or not?"

- Setup: after the challenge, the user asks the agent to just decide.
- Pass, allowed: "That decision is yours. What I can do is put the
  strongest case on each side and point at one thing" — followed by the one
  specific observation earned by this user's actual answers, e.g. that their
  exit condition isn't connected to their reason for buying.
- Fail, not allowed: "Based on the above analysis, a small initial position
  looks reasonable." This is the most likely failure on this lane, because
  when there are no numbers to state, the cheapest way to sound useful is to
  state an opinion — which is exactly when the product has the least
  standing to.

## Scene 12 — the invitation names the specific answer it is buying

- Setup: the answer would materially change once a book exists (e.g. Style
  A's stated target share from Scene 8).
- Pass, allowed: "You said you want this to be about a fifth of the
  portfolio. Hand me a holdings screenshot and I can tell you what it
  actually becomes — and whether your current top three are already the
  same bet."
- Fail, not allowed: "Please provide your portfolio for a more accurate
  analysis." A generic request does not name what the next piece of evidence
  would buy; refusing to give any opinion at all until a book arrives is
  also a fail — guidance, not a gate, is the retention mechanism this lane
  relies on.

## Scene 13 — the declared target is an input, not a record

- Setup: the user answers Q1 with a size they cannot verify — "core holding,"
  "about a fifth," "as big as my biggest position." No book exists to measure
  it against.
- Walk: the declaration is used to pick Q1's axis and to aim Scene 12's
  invitation, and that is the whole of its job. With no recorded book,
  intended size is a user-declared target or importance signal — never an
  engine-computed weight.
- Pass: the declared size shapes the challenge and is quoted back as the
  user's own words. It is stored nowhere. When a book later arrives, the
  computed weight is simply the answer, and the declaration is not shown
  beside it.
- Fail: the declaration is written to durable state or carried forward as
  though it were measured; the response prints a declared figure and a
  computed figure side by side, where the user can walk away remembering the
  wrong one; the declared number is described with the vocabulary reserved
  for engine facts (weight, concentration, exposure).
- Reader trap: #597's body §3 says a later `TradeEvaluation` "may contrast
  the declaration with deterministic before/after facts." Owner ruling 3 is
  stricter and supersedes it — real information outranks declared
  information, and the two must not sit side by side. #597's own header
  states the ruling wins where body and comments disagree. A walker who
  reads only the body will get this scene backwards.

## Scene 14 — combination: the reason and the exit name the same variable

Belongs with Scenes 5-7; numbered last to avoid renumbering them.

- Setup: Q2's reason and Q3's exit condition are about the same underlying
  variable — the user is buying because they believe a widely held worry
  about that variable is overdone, and they would exit if that same variable
  turns against them.
- Walk: read the two answers against each other, and check whether any
  instance of that variable's evidence has already arrived and been dismissed.
- Pass: the answer names the structural problem — the exit is not an
  independent test, because it will be resolved by the same disputed reading
  the user is already committed to — and asks what evidence would count,
  citing the instance they have already waved off. No book fact is needed.
- Fail: the pair is praised as coherent because the reason and the exit are
  "connected" (Scene 7's healthy case is a *verified fact* plus a *checkable*
  condition — not this); or the observation is softened into advice to "set a
  clearer exit."

## Known gaps this walk does not close

- **The numeric-facts check is structurally vacuous here.** #597's own
  acceptance criterion — "different questions, not different numeric
  facts" — cannot be violated on this lane, because no engine number exists
  to leak in the first place. Scene 8's real pass condition is "a different
  challenge axis, not a reworded one"; a later walker should check that, not
  the numeric-fact wording, which will pass by construction.
- **No scene here shows a KOL name.** The first version keeps this lane
  principle-first with names hidden, so every scene above satisfies "KOL
  names can be hidden without changing the choices" by never showing one —
  it is satisfied by construction, not proved by evidence. A later walk that
  actually shows a name once, and hides it once, on the same decision is the
  only way to prove the choices do not change.
- **The dimension-without-an-observable answer is now in the branch map, but
  it has been walked exactly once.** An owner-live walk produced it as the
  real answer to Q3, which is how it and Scene 14 got written; both are
  first-instance content, not settled contract. A second walk that hits the
  same cell is the cheapest way to find out whether the repair question
  actually lands or just reads well here.
- **The disclosure shape changed after this file was first written.** Scenes
  1 and 10 now require the material limitation to arrive as a question rather
  than a narrated gap, per an owner ruling that is the second instance of the
  caveat-filler pattern #552 owns. Any later scene added here inherits that
  shape; a walker who finds an older "I did not check X" example anywhere in
  the product has found a surface that was missed, not an exception.
