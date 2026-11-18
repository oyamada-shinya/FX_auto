# FX market research

Research and simulation only. There is no brokerage client or order execution.
Public output contains market health and indicators, never personal balances,
positions, risk thresholds, credentials, or personalized simulation output.

Pairs: USD/JPY, MXN/JPY, AUD/USD, EUR/USD, AUD/JPY.
Frames: 5m, 15m and complete UTC-anchored 4h bars aggregated from hourly input.

## Setup and verification

Use Python 3.12 or newer. Windows: `setup.bat`. Linux: `sh setup.sh`.
Both create `.venv`, install pinned direct dependencies and run offline tests.
Direct and resolved transitive versions are constrained by `requirements.lock.txt`.
This is a version lock, not package-hash verification.
Run live research with `.venv/Scripts/python fx.py` (Windows) or
`.venv/bin/python fx.py` (Linux). No account or provider key is required.

## Data and indicator conventions

Only closed bars enter calculations. Reject duplicates, missing/nonfinite OHLC,
invalid OHLC ordering, timezone-naive/NaT/future/off-grid timestamps and intraday
holes. Cross-date gaps are disclosed and block quality eligibility until a verified
session calendar classifies them. No synthetic fill or unconditional row deletion.
Raw hourly OHLC is validated before 4h aggregation; incomplete bins are excluded.
WMA uses ascending weights (newest largest); ATR14 uses first-bar H-L, the first
14 true ranges as arithmetic seed and Wilder smoothing; BB20 uses population
standard deviation. MACD is the research convention 12/26/9 with first-observation
EMA seed (`adjust=False`); exact broker-chart equivalence is unverified.
History length affects ATR/MACD warm-up and initial values.

Retrieval has up to three attempts, request timeout and bounded backoff. A failed
or stale stream makes the live job fail. Reports are serialized before atomic
replacement; exception text is excluded from the public error envelope.

## Acceptance boundary

`new_orders_allowed` is always false. `decision=HOLD` and
`acceptance=NOT_EVALUATED` are independent of a successful data collection.
An unexplained historical gap, unknown session calendar, insufficient real history,
unvalidated costs, private portfolio state or notification delivery prevents acceptance.
Offline synthetic tests are correctness/stress checks, not evidence of profitability.
Yahoo 5m/15m history cannot establish a minimum 90-day backtest; a licensed,
provenance-bearing historical source with bid/ask and broker cost evidence is required.
No personal profile is passed to public Actions. Keep all private inputs and derived
results outside the repository, or under ignored `private/` and `backtest-results/`.
Never upload those directories to public Actions.

## Operations and notifications

Actions runs tests before live collection and preserves public `report.json` even
on failure. Hourly scheduling is requested at minute 13; scheduling punctuality and
continuous availability are not guaranteed. A push run does not prove hourly uptime.
The existing ChatGPT notification task must be identified and its consumer tested
before notification integration can be declared complete. No duplicate task is created.
Consumers must check workflow conclusion, exact commit, schema, completeness and
report age, and keep HOLD on missing/failed/stale/unknown data.

## Private simulation and candidate research

`python research.py --config <external-config.json> --output <private-result.json>`
loads explicit profile, costs, finite settings and synchronized UTC OHLC CSV files.
The CLI prints only a redacted HOLD status; detailed results stay in the requested
private file. No personal numeric configuration is distributed. See
[acceptance and input contract](docs/acceptance.md) and module docstrings for keys,
cost units, provenance and known limitations.

`strategy.generate_candidates` applies a documented research hypothesis using
agreement across the three frames. Every search range, quantity and inventory cap
is supplied externally. This is not a proven profitable rule or a broker order.
`strategy.walk_forward` selects a finite candidate using each training fold only
and evaluates the following disjoint test fold. Callers must generate candidate
values from training history only; the helper cannot certify an externally supplied
candidate's origin. Insufficient historical coverage blocks evaluation. Synthetic
scenario mode never yields economic acceptance. The simulator's UTC-day swap and
intrabar stress assumptions do not reproduce the broker's rollover/quote feed.
Real calibrated correlation, event regimes, allocation and optimality remain unverified.

## Broker sources

[Matsui FX rules](https://www.matsui.co.jp/fx/rule/) define normal spreads for repeat
orders, minimum pip widths, margin, order counts and setting-change restrictions.
Provider mid-price research is not a Matsui executable quote. Changed range/spacing
is a replacement scenario; never assume existing positions disappear.
