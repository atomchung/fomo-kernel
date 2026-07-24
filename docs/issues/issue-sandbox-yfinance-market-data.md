# Issue: Sandbox Environment Network Restrictions & Yahoo Finance Fallback Strategy

**Status**: Open / Under Discussion  
**Created**: 2026-07-22  
**Component**: `engine/trade_recap.py` (`fetch_prices`), `engine/market_context.py`, Data Pipeline  

---

## 1. Context & Problem Statement

`fomo-kernel` currently relies on `yfinance` (`yf.download`) to fetch historical daily price series, current market prices, FX rates, and benchmark ETF performance (e.g., SPY, QQQ, ^TWII).

When users execute `fomo-kernel` within **air-gapped AI sandbox environments** (e.g., ChatGPT Work / Code Interpreter / Claude Cowork / WebAssembly Sandboxes), **all outbound HTTP/socket connections to Yahoo Finance fail**.

### Why Yahoo Finance remains desirable:
1. **Zero Financial & Maintenance Cost**: Free, open, no API key required for users.
2. **Comprehensive Historical Data**: Provides daily close, adjusted price, dividend adjustments, FX rates, and major global benchmark indexes.
3. **No User Friction**: Users don't need to register for financial API keys (e.g., Alpha Vantage, Polygon, Financial Modeling Prep).

---

## 2. Critique of Unconstrained Agent Search (Ad-hoc Web Fetching)

An initial proposal suggested letting the host AI Agent fetch prices via web search when `yfinance` fails inside the sandbox. **This approach has severe flaws and is rejected as a primary solution**:

* **Lack of Timestamp Alignment (timeliness and time-slice misalignment)**:
  Prices fetched via LLM search lack consistent timestamps (e.g., intraday vs. market close, adjusted vs. unadjusted, timezone mismatches between benchmark ETFs and individual equities).
* **Missing Historical Series**:
  LLM web search cannot reliably retrieve complete daily price time-series needed to compute `post-exit performance` (e.g., N-day price movement after exit).
* **Violation of Core Product Policy**:
  Rule #1 explicitly states: *"Numbers, rankings, cycle IDs, metrics, weights, and ETF exemptions come from code. The agent must not calculate, invent, or alter derived values."* Relying on LLM search results introduces hallucination risks and unverified inputs into deterministic engine logic.

---

## 3. Trade-off Analysis & Candidate Options (Pros & Cons)

### Option A: CLI Helper Pre-fetch (High User Friction for Web Sandboxes)
* **Description**:
  Run a local CLI command to pre-fetch market data into `market_snapshot.json` before uploading to the sandbox.
* **User Experience & Friction Analysis**:
  * **High Friction**: For users in ChatGPT Work or Claude Cowork (Web UI), asking them to open a local terminal, run a CLI script, and drag a second JSON file into the browser **completely breaks the seamless LLM chat experience**.
  * **Verdict**: Not suitable as a default/primary workflow; acceptable only as a power-user developer shortcut.

### Option B: Scoped LLM Agent Web Search (Google / Yahoo Finance Only)
* **Description**:
  While the Python execution environment lacks network access, the host LLM Agent in many environments retains Web Browsing tools. The Agent can be strictly prompted to search designated financial portals (e.g., `Google Finance` or `Yahoo Finance`) specifically for current holding prices, then inject a normalized `--prices-json` payload into the engine.
* **Strict Constraints & Rules**:
  1. **Strict Source Scoping**: Limit queries exclusively to authoritative financial pages (e.g., `google.com/finance/quote/SYMBOL:MARKET` or `finance.yahoo.com/quote/SYMBOL`).
  2. **Strict Schema & Timestamp Required**: The Agent must extract:
     `{"ticker": "NVDA", "price": 125.50, "as_of": "2026-07-22", "currency": "USD", "source": "Google Finance"}`.
  3. **Scope Limitation**: Used **only for current held position prices (`current_price`)**. Do NOT attempt to fetch 90-day daily time-series via search (to prevent hallucination and multi-query timeouts).
* **Pros**:
  * **Zero Friction for Web Users**: User just uploads the CSV; Agent handles price lookup in the background seamlessly.
  * Solves Python sandbox network restriction while keeping prices tied to reputable sources.
* **Cons**:
  * Cannot fetch full historical daily K-lines for post-exit performance.
  * Dependent on the host LLM platform supporting Web Browsing.

---

## 4. Multi-Tiered Market Data Strategy (Proposed Architecture)

To support all environments seamlessly, `fomo-kernel` will implement a **4-Tier Layered Strategy**:

1. **Tier 1 (Direct yfinance Engine)**: Active in local CLI/IDE with open network. Engine fetches full daily time-series, benchmark ETFs, and current prices.
2. **Tier 2 (Scoped Agent Search)**: Active in Web Sandboxes where LLM browsing is available. Agent fetches current prices from Google/Yahoo Finance and passes `--prices-json` to the engine.
3. **Tier 3 (Broker-Declared Prices)**: Active when user CSV / Snapshot envelope explicitly contains broker-declared `current_price`.
4. **Tier 4 (Bounded Review Fallback)**: Active when no price sources are available. Engine fails closed to a **Bounded Review** (diagnoses closed trades with 100% precision, skips unpriced unrealized metrics gracefully).
