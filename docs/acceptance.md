# Research acceptance and evidence

This project cannot submit orders. A successful data pipeline or synthetic test
suite does not establish that any strategy satisfies a private economic target.
Private account values and derived portfolio reports must never appear in this
public repository or its Actions logs/artifacts.

## Implemented checks

- Raw closed-candle OHLC integrity, finite values, unique/aligned UTC timestamps,
  explicit gap eligibility, and complete hourly constituents before 4h aggregation.
- Independent numerical oracles for WMA, Wilder ATR, population BB and seeded MACD.
- Bounded provider retrieval/backoff, explicit failure envelope and atomic JSON.
- Decimal current-snapshot risk with pending-order margin subtraction, floating
  loss, equity drawdown, maintenance and gross inventory caps.
- Broker-setting format checks for tick/pip units, minimum spacing/profit width,
  quantity, stop orientation and bounded ladder size. Passing format checks is not
  authorization or proof of exact Matsui order-generation behavior.
- Research-only finite grid simulation with costs, adverse same-bar execution,
  gap handling, marked open inventory, synchronized USD/JPY conversion and shared
  portfolio limits. Its assumptions and blockers are returned with every result.
- A local CLI accepts external private configuration and synchronized UTC CSVs;
  derived results are restricted to private output paths.
- A notification payload consumer checks exact workflow metadata, schema,
  completeness, expiry, numeric finiteness and a public allowlist. It sends nothing.

## Evidence and unresolved gates

| Gate | Evidence | State |
|---|---|---|
| Initial source/log inspection | Baseline `8c9865f`, original Actions `44307850458` | Inspected |
| First revised remote run | `44310511283`, commit `84f8a05`; Linux tests passed | Partial verification |
| First revised live data | Quality failed; JSON failure artifact saved | Failed, not accepted |
| Required long history | Existing 5m/15m provider retention insufficient; alternative bounded probes failed | Blocked |
| Exact session calendar | Weekend/holiday/maintenance classification unverified | HOLD |
| Historical bid/ask, slippage and swap/rollover | Sufficient dated broker evidence unavailable | HOLD |
| Historical OOS and walk-forward performance | No qualifying dataset has been evaluated | Not performed |
| Economic releases and all requested regimes | Real timestamped event/regime evidence unavailable | Not accepted |
| Existing ChatGPT notification | Shared output identified; no authenticated editable task/delivery receipt | Not integrated |
| Continuous hourly operation | Push tests cannot prove scheduled uptime | Not verified |

## Data requirements

Minimum actual historical span is 90 days, preferably at least 365, plus indicator
warm-up. Require source/licence, timezone, price side, checksums, expected sessions,
missingness and bid/ask/cost/rollover coverage. A sparse date span is not coverage.
Synthetic scenarios and current Yahoo samples must not be relabeled as this test.
Do not bulk-download or redistribute data without applicable permission.

## Local private research input

Run `python research.py --config <external-config.json> --output <private-result.json>`.
The config has `profile`, `settings`, `costs`, and `data` objects; `data` maps each
pair to an OHLC CSV path relative to the config. Each CSV requires `timestamp`
(explicit UTC), `Open`, `High`, `Low`, and `Close`. No private numeric example is
distributed. Exact required keys and units are documented in the pure modules.
Historical mode stops on insufficient or unclassified coverage. Explicit scenario
mode exists for deterministic tests and never yields economic acceptance.

## Resume operations

1. Supply a permitted, sufficient five-pair historical dataset and dated broker
   cost/rollover evidence through external private files.
2. Verify session/holiday and economic-release calendars; classify rather than
   silently remove missing observations.
3. Freeze candidate search space and private risk/target configuration; evaluate
   training selection, untouched OOS and chronological walk-forward results.
4. Keep risk limits fixed; report failed goals and no-trade alternatives honestly.
5. In an authenticated existing ChatGPT task, configure the read-only consumer
   contract and verify a real success/failure/recovery notification. Never infer
   task-edit access from a public shared transcript or create a duplicate task.

Broker rule source: https://www.matsui.co.jp/fx/rule/
Historical API reference: https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html
