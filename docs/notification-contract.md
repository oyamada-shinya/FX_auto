# Existing ChatGPT task integration contract

The pure Python consumer is implemented; no task has been edited and no message
has been sent by this code. An actual delivery receipt is required for acceptance.
Do not create a duplicate scheduled task to bypass unavailable edit access.

## Read-only consumer sequence

1. Resolve the intended repository branch head through a trusted GitHub connection.
2. Select a completed FX research run for that exact head; check conclusion and
   latest attempt, not a previously successful older revision.
3. Fetch `report.json` from that exact run/attempt artifact. A consumer must establish
   artifact provenance; an arbitrary JSON dictionary is not authenticated by this helper.
4. Call `notification.validate_notification(report, run, now, expected_commit=head,
   seen_event_keys=delivered_events)` with trusted run metadata and UTC current time.
5. On any rejection, show a static HOLD reason without old rates or settings.
6. Valid output is research-only and still HOLD; it never authorizes a new order.
   Show source execution time, candle close time and age, and distinguish current
   observations from old references. Never add private profile/account data to public
   GitHub logs, artifacts or this public consumer payload.
7. Store the event key only after confirmed notification delivery. Durable state and
   actual ChatGPT delivery are owned by the authenticated existing task, not this helper.
8. If the hourly run is missing, disabled, delayed or failed, report that condition and
   prohibit stale reuse. Do not equate a configured schedule with proven hourly uptime.

## Required end-to-end evidence

- Authenticated edit of the identified existing task, preserving its schedule.
- Exact-commit live data success through the consumer to a real delivered notification.
- Failed data retrieval, stale report, missing run and duplicate-event cases.
- Recovery on a new complete fresh execution; no brokerage restart or order submission.
- Delivery acknowledgment and no disclosure of private portfolio inputs/results.

A public shared transcript is evidence of previous output, not editable-task access,
an automation identifier, or permission to invent credentials. Until the authenticated
target is accessible and the above cases run, integration remains incomplete.
