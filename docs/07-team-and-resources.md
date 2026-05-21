# 07 — Team and Resources

This document lists what we need — people, tools, infrastructure — to build Phase 1 in 12 weeks and run the pilot.

## Team composition (Phase 1)

A lean cross-functional team. Five builders + one domain expert.

| Role | Headcount | Phase 1 commitment | Why we need them |
|---|---|---|---|
| **Tech Lead / Full-stack engineer** | 1 | Full-time | Owns architecture, sets code standards, builds across the stack, makes the hard calls. |
| **Backend engineer** | 1 | Full-time | Owns API, data layer, ingestion service, understanding service. Python + Postgres + Celery. |
| **AI / ML engineer** | 1 | Full-time | Owns extraction pipeline, classification, anomaly detection, forecasting. Comfortable with OCR + LLMs + classical ML. |
| **Frontend engineer** | 1 | Full-time | Owns dashboard, inbox UI, mobile-responsive layout. React + TS + Tailwind. |
| **Product designer** | 1 | Part-time (50%) | Owns the founder's experience: dashboard layout, insight card design, inbox flows. |
| **CA / Finance domain expert** | 1 | Part-time (20–30%) | Validates that the insights are correct, complete, and tax-compliant. Trains the team on accounting realities. |

Total full-time equivalents: **~4.5 FTE** during Phase 1.

### Roles we deliberately do NOT hire in Phase 1

- **DevOps / SRE** — Tech Lead handles infra with Terraform + managed AWS services. Dedicated SRE comes when we hit ~20 paying customers or before SOC 2 work.
- **QA engineer** — Devs write tests; we use Playwright for E2E. Dedicated QA after Phase 1.
- **Customer success** — In Phase 1 the founder + tech lead support pilot customers directly. This is also the best feedback channel.
- **Sales** — Pilots are founder-led. Sales team in Phase 2.
- **Marketing** — Light content + LinkedIn presence run by founder. Dedicated marketer in Phase 2.

## Tooling and software

Day-one tools every team member needs access to:

| Category | Tool | Monthly cost (5 seats, INR approx) |
|---|---|---|
| Source control | GitHub Team | ₹1,800 |
| Project management | Linear | ₹3,000 |
| Documentation | Notion | ₹2,000 |
| Comms | Slack | ₹3,500 |
| Design | Figma | ₹4,500 |
| Password mgmt | 1Password Teams | ₹1,500 |
| Error tracking | Sentry (team) | ₹2,500 |
| Monitoring | Grafana Cloud (free tier → pro mid-Phase 1) | ₹3,000 |
| AI dev assistant | Claude (Pro / Max) | ₹15,000–30,000 |
| **Subtotal** | | **~₹37,000 / month** |

## Infrastructure spend (already in `05-tech-stack.md`)

Recap: **~₹60,000 – ₹95,000 / month** for AWS + LLM APIs + OCR + supporting services during pilot (under 10 customers).

## One-time spend

| Item | Estimated cost (INR) |
|---|---|
| Domain (.com + .in) | ₹3,000 |
| Trademark filing (Nira Insig) | ₹15,000 |
| Legal review (Terms, Privacy, DPDP) | ₹50,000–₹1,00,000 |
| Logo + brand kit | ₹40,000 |
| Initial security review / pen-test | ₹1,50,000–₹2,50,000 |
| **Total one-time** | **~₹2.5 – 4.5 lakh** |

## Phase 1 budget summary (12 weeks)

Rough envelope, all-in, in INR. Adjust based on actual salaries.

| Bucket | Monthly | 3-month total |
|---|---|---|
| Salaries (4.5 FTE blended) | ₹12,00,000 – ₹18,00,000 | ₹36,00,000 – ₹54,00,000 |
| Tooling | ₹37,000 | ₹1,11,000 |
| Infrastructure + APIs | ₹95,000 | ₹2,85,000 |
| One-time spend (amortized) | — | ₹3,50,000 |
| Contingency (15%) | — | ₹6,50,000 – ₹9,50,000 |
| **Total Phase 1** | | **~₹50 – 71 lakh** |

This is a *planning estimate*. Real numbers will depend on hires (Bangalore vs Tier-2 cities; in-house vs contract).

## Hiring sequence

If hiring from scratch, recommended order:

1. **Week –4:** Tech Lead. Without this, nothing else works.
2. **Week –3:** AI/ML engineer (long lead time; specialist role).
3. **Week –2:** Backend + Frontend engineers (can overlap).
4. **Week –1:** Designer (part-time).
5. **Week 0:** CA / Domain expert (on retainer).

In total: aim for full team in place by Week 0, build kicks off Week 1.

## What the CEO has to provide

To unblock the team, the CEO needs to commit to:

1. **Budget approval** — sign-off on the Phase 1 envelope above.
2. **Hiring authority** — final say on offers; expedite when needed.
3. **Pilot customer introductions** — 5–8 warm intros to founder-led businesses that match our target profile (₹2–50 Cr revenue, paper-heavy ops). The team will close at least 3 of these as pilots.
4. **Weekly check-in cadence** — 30 mins/week with the Tech Lead. Not more (don't slow them down), not less (don't lose touch).
5. **Decision SLA** — any blocking decision flagged to the CEO gets resolved within 48 hours.

## What the team needs from the CEO (cultural commitments)

Beyond budget and intros, the team needs:

- **A shippable bar, not a perfect bar.** Phase 1 is about getting real users on the product. Polish comes from feedback, not from pre-launch refinement.
- **Defense against scope creep.** Every "small extra feature" delays the pilot. The CEO is the scope guardian.
- **Permission to say no.** To inbound customer requests that fall outside the Phase 1 scope. We capture them in the backlog; we don't build them yet.
- **Honest review of pilot signal.** Phase 2 plans must be informed by what pilots actually do and say, not by what we hoped they would.
