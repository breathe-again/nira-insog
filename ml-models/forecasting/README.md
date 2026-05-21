# ml-models/forecasting/

Predictive models powering the "future insights" layer.

Models:

- **Cash flow forecast** — Prophet model trained per organization on their bank transaction history. Output: 30/60/90 day projected cash with confidence bands.
- **Receivable collection probability** — gradient boosting classifier. Output: probability an overdue invoice gets collected in the next 30 days.
- **GST projection** — run-rate model with seasonality adjustment.
- **Vendor cost trend** — linear trend with significance test per vendor.
- **Client risk score** — logistic regression on payment-behavior features.

Each model has training, evaluation, and serving code. Models are retrained weekly (or on-demand when an org has enough new data).
