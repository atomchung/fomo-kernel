# Issue lifecycle and context loading

GitHub's `open` state answers one question: has this been closed. It does not say whether the work is on the current critical path, parked behind a decision, kept only so a rejected design is not proposed again, or already shipped with the evidence buried in comment 7.

Treating `is:open` as the execution queue has a measurable cost in this repository: superseded designs get re-proposed, completed work gets reimplemented, a P2 edge case gets mistaken for the milestone's next step, and every fresh contributor or agent spends its first working hour reconstructing history instead of advancing the product.

This file is the single authority on how issues are kept readable and how repository context is loaded. Other surfaces point here; they do not restate it.

## Three separate things

| Concept | What it is | Where it lives |
|---|---|---|
| **Open backlog** | Every unresolved or deliberately preserved record. | `is:open` |
| **Active execution queue** | At most one or two implementation fronts plus their acceptance gate. | The current-critical-path section of the context index |
| **Context index** | A small routing surface naming the current milestone, its owning issues, and the next smallest step. | Issue #27 |

An issue being open does not put it in the execution queue. Membership is stated in the context index, not inferred from state, label, or priority prefix.

Priority is not milestone ownership. A confirmed P1 defect that the milestone owner has not promoted is real debt and still not the next step.

## The body is the current summary

Comments carry chronology, discussion, and evidence, and are never rewritten. But routing truth cannot live only in a thread: any reader — human or agent — that retrieves the body and not all 14 comments must still get a correct task.

So every issue that stays open leads with:

```md
## Status
Implementation | Blocked | Discussion | Research only | Parked | Deferred | Superseded

## Milestone
Current: M0 | Future: M1/M2/... | None

## Owning outcome
The user or system result this issue owns.

## Current scope
Only the remaining work. Completed or re-homed work is struck or removed from active scope.

## Current evidence
Latest PR, commit, test, or owner ruling — with identifiers.

## Dependencies
Owning issue, preceding or following leaf, or acceptance gate.
```

The historical body is preserved below the header rather than deleted, under a heading that marks it as history. Git and GitHub keep every prior revision, so narrowing an issue is reversible; leaving a stale body in place is not free.

### Status vocabulary

- **Implementation** — someone can start coding from this body today.
- **Blocked** — ready except for a named dependency; the dependency is in `Dependencies`.
- **Discussion** — the product question is open; a design ruling is what unblocks it, not a PR.
- **Research only** — evidence to consult before a decision. Never an implementation request.
- **Parked** — deliberately not now, with the condition that would restart it.
- **Deferred** — will happen, after a named milestone.
- **Superseded** — a later decision replaced this design. The header says `Superseded by #NN`, prominently. Recording that only in a comment is the failure this vocabulary exists to prevent.

## Default context-loading order

For substantive work, load in this order and stop when you have enough:

1. latest `main`, `README.md`, `AGENTS.md`, and the current owning contract or docs;
2. the current roadmap and scope guard;
3. the context index (#27) for the current milestone and critical path;
4. the owning implementation issue and its acceptance issue;
5. only the direct dependencies or leaves those two reference;
6. the current open PR, if one exists.

**Do not bulk-load every open issue as context.** Search the wider backlog for a specific reason: checking for a duplicate before opening one, finding a prior decision on a question you are about to re-answer, or resolving a named dependency.

An issue's title prefix (`[design·P2]`, `[research·roadmap]`, `[QA·M0]`) is a convention, not an enforced schema. Read the `Status` header before acting on a title.

## Before opening an issue

1. Search first — `gh issue list --search`, and `git log --grep` for the same root cause.
2. If a duplicate exists, comment on it instead. A second symptom of the same defect is evidence, not a second issue.
3. If it belongs to an existing owner's scope, put it there rather than creating a sibling that competes for the same critical path slot.
4. Open a new issue when the work has a distinct owning outcome and no existing home. Give it the header above at creation time.

## Closing and preserving

The goal is quarantine from the default execution context, not a maximum closure rate.

- Close completed leaves.
- Close duplicates as `duplicate`, pointing at the survivor.
- Close abandoned proposals as `not_planned`, pointing at the replacement.
- **Do not close** a record that would erase a decision a future contributor could otherwise repeat. Mark it `Superseded`, `Parked`, `Deferred`, or `Research only` and leave it open — those are records, and the loading order above already keeps them out of the execution queue.
- No issue is closed for being old, low priority, or inconvenient to read.
- Closing a roadmap or epic record requires owner approval and must link the replacement authority.

## Why there is no checker for this

A mechanical check can verify that a `Status` header exists. It cannot verify that the stated status is true, which is the only property that matters here. Adding one would produce a green suite over a backlog that still lies.

Add a checker only if a specific failure repeats after this guideline is in use — the repository's standing rule that the second recurrence of a symptom is a design problem rather than a second patch ([development-guide.md](development-guide.md)).

## Ownership

- **#27** — current context index. Routing only; owns no implementation.
- **#538** — this contract and the issue-hygiene cleanup passes.
