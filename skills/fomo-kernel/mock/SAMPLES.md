# Mock portfolio fixtures

These synthetic long-only BUY/SELL CSV files exercise stable behavioral branches. They contain no real user data. Driver-map files provide deterministic sector and theme classification for less common tickers.

## Run a fixture

```bash
cd skills/fomo-kernel
TR_DRIVER_MAP=mock/sample_fundamental.driver_map.json python3 engine/trade_recap.py mock/sample_fundamental.csv
TR_DRIVER_MAP=mock/sample_momentum.driver_map.json python3 engine/trade_recap.py mock/sample_momentum.csv
TR_DRIVER_MAP=mock/sample_value.driver_map.json python3 engine/trade_recap.py mock/sample_value.csv
```

Use the matching `sample_<name>.driver_map.json` for other personas. The engine treats a fixture exactly like any other input; it does not infer demo mode from a filename. Test-drive labeling and persistence isolation belong to `review.py prepare --test-drive`.

Live market-dependent values may drift because online runs fetch historical and latest prices. The primary behavioral branch for each fixture must remain stable and is covered by offline tests.

## Baseline personas

| Fixture | Behavior design | Expected primary branch |
|---|---|---|
| `sample_fundamental.csv` | Diversified, moderate positions, sells winners sooner than losers | exit discipline |
| `sample_momentum.csv` | Concentrated AI/semiconductor exposure, large positions, short holds | sizing and driver concentration |
| `sample_value.csv` | Repeated adds to losing positions and small realized gains | losing-position adds, then sizing |

## Extended personas

| Fixture | Behavior design | Expected primary branch |
|---|---|---|
| `sample_ai_holder.csv` | Long-duration exposure to several tickers sharing one AI narrative | driver concentration |
| `sample_oldecon.csv` | Diversified traditional sectors with restrained sizing | strength-first clean baseline |
| `sample_swing.csv` | Short winners and much longer losing holds in the same instruments | inconsistent holding horizon |
| `sample_day_trader.csv` | Same-day entries and exits across several tickers | overtrading/holding period |

`sample_ai_holder.csv` can shift between diversification and sizing in live-price runs because a large winner changes market-value weights. Both conclusions describe the same underlying single-narrative concentration. Offline regression uses deterministic cost-basis behavior.

## Engine boundary fixtures

| Fixture | Boundary under test | Key expectation |
|---|---|---|
| `sample_pyramid.csv` | Adds only to winning positions | must not be labeled averaging down |
| `sample_insufficient.csv` | Fewer than three round trips and a short span | commitment remains null by default |
| `sample_noisy_broker.csv` | Dividends, transfers, fees, and reinvestment rows | behavior matches the clean baseline after filtering |
| `sample_rotator.csv` | Full-position rotation through unrelated hot themes | sequence exposes theme churn even when current snapshot is simply concentrated |
| `sample_panic_seller.csv` | Several long-held losing positions exited in one stress window, followed by a higher re-entry | extreme exit-discipline branch |
| `sample_tw_mixed.csv` | Taiwan and US instruments with multiple currencies | per-market benchmark and aggregate-currency contracts |

## Fixture design rules

- Make one behavioral leak dominant and keep unrelated dimensions controlled.
- Use real tickers and historically plausible dates/prices when online price paths matter.
- Avoid delisted instruments and uncontrolled corporate actions.
- Keep both sides of a synthetic round trip on the same side of a split date whenever possible. Offline runs do not fetch split history, while online runs do; cross-split fixtures cannot be naturally scaled in both modes.
- If a split is intentionally tested, document the nominal versus split-adjusted representation and cover both offline and online assumptions explicitly.
- Keep duplicate rows distinguishable. The loader deduplicates on symbol, side, quantity, price, and date.
- Prefer differential assertions for noisy-input fixtures: their output should match the equivalent clean fixture.
- Keep metadata about simulation and state isolation outside the rendered card.

## Privacy

Only synthetic fixtures may be committed. `.gitignore` blocks other CSV files. Never copy a real user's transactions into a fixture, issue, screenshot, or expected-output file.
