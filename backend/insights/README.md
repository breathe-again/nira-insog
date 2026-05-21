# backend/insights/

Reads the structured warehouse and produces insights (descriptive + predictive).

Components:

- Aggregation jobs (cash position, in/out trends, aging buckets, top vendors/clients, expense categories, GST summary).
- Anomaly + alert generator.
- Predictive models (cash flow forecast via Prophet, collection probability classifier, GST projection, vendor cost trend, client risk score).
- Insight feed: writes `Insight` records consumed by the dashboard.
- Weekly digest email composer.

Runs on a mix of scheduled jobs (every 15 min) and reactive triggers (new document indexed).
