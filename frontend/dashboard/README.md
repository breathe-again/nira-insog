# frontend/dashboard/

The "Altogether" dashboard — one consolidated view of the financial truth of the business.

Components (planned):

- `CashPositionCard` — current cash across accounts, week-over-week delta.
- `CashFlowChart` — money in / money out, with 7d / 30d / 90d / 12m views.
- `ReceivablesAging` — table of overdue invoices grouped by age bucket.
- `TopVendorsCard` and `TopClientsCard`.
- `ExpenseBreakdown` — category pie + table.
- `GstSummary` — quarter-to-date liability with projection.
- `InsightsFeed` — ordered cards (urgent → info), each dismissible and traceable.
- `CashForecastChart` — predictive line chart with confidence band.

Every card supports drill-down to source documents.
