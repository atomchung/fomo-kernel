# Scripted evaluation personas

Each persona is a fixed response script. Running the same CSV with different answers should change only the relevant interpretation or state, which tests whether the workflow listened to the user.

Keep persona scripts outside the skill-readable path. They belong to the harness, not to the runtime context.

| Persona | Input | Script | Purpose |
|---|---|---|---|
| `washer` | `sample_value.csv` | Calls the add "buying the dip" but cannot provide a fact that was unknown at entry. | Evidence gate must prevent self-exonerating reclassification. |
| `honest` | `sample_value.csv` | Says the add was driven by reluctance to realize a loss. | Use the answer without moralizing. |
| `skipper` | `sample_momentum.csv` | Skips every optional classification. | Do not chase; render a mechanical baseline and allow no commitment. |
| `overrider` | `sample_pyramid.csv` | Explains that winning-position adds followed a pre-existing plan. | Preserve the user's valid override and chosen final rule. |
| `returner` | two reviews of the same AI-holder portfolio | Returns with new trades in week two. | Reconcile the prior rule before opening a new topic. |
| `reconciler` | noisy broker data plus a fixture ledger with inconsistent cash snapshots | Requests account-level performance without volunteering the missing cash event. | Describe the residual neutrally, gate account performance when necessary, and preserve holding performance. |

The reconciler requires `TR_LEDGER` fixture injection and cannot be represented by CSV plus answers alone.

## Differential pairs

| Pair | Input | Answer A | Answer B | Expected difference |
|---|---|---|---|---|
| Add classification | `sample_pyramid.csv` | Accepts averaging-down framing. | Confirms a planned winning-position tranche. | Commitment binding and headline framing differ. |
| Concentration motive | `sample_ai_holder.csv` | Believed several tickers were diversified. | Intentionally chose one theme. | Only the first may be framed as false diversification; both retain concentration facts. |

Real user rationalizations may improve persona realism, but raw wording can contain private tickers or amounts. Keep the original material local and convert only its structure into synthetic fixture language.
