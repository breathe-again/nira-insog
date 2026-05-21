# 05 — Technology Stack

Concrete technology choices for each layer of the system. For every choice we list the pick, the alternatives, and a one-line reason.

The guiding principle: **boring, proven, hireable**. We are building a product, not showcasing technology.

---

## Frontend

| Concern | Choice | Alternatives considered | Why |
|---|---|---|---|
| Framework | **React 18** with TypeScript | Vue, Svelte, Next.js | Largest hiring pool; team familiarity; rich ecosystem. |
| Bundler / dev server | **Vite** | Webpack, CRA | Fast dev loop; modern defaults. |
| Styling | **Tailwind CSS** + Headless UI | Styled-components, CSS Modules | Speed of UI iteration; consistent design system. |
| Component library | **shadcn/ui** | MUI, Ant Design | Tailored, copy-in components; full design control. |
| State management | **Zustand** + React Query | Redux, MobX | React Query for server state, Zustand for UI state — minimal boilerplate. |
| Charts | **Recharts** | Chart.js, D3 | React-native, declarative; sufficient for dashboard needs. |
| Forms | **React Hook Form** + **Zod** | Formik | Zod schemas double as API contracts. |
| Routing | **React Router v6** | TanStack Router | Mature, well-known. |
| Real-time updates | **WebSocket** (native) via API | Pusher, Ably | Lower cost; we control the stack. |

## Backend

| Concern | Choice | Alternatives considered | Why |
|---|---|---|---|
| Language | **Python 3.12** | Node.js, Go | ML + AI libraries live in Python; faster to integrate LLM/OCR. |
| Web framework | **FastAPI** | Django, Flask | Async, type-safe (Pydantic), auto OpenAPI docs. |
| ORM | **SQLAlchemy 2.0** + **Alembic** migrations | Tortoise, Prisma | Mature; powerful for the relational workload. |
| Auth | **Authlib** (OAuth) + JWT sessions | Auth0, Clerk | Self-hosted for cost and data control; revisit if support load grows. |
| Background workers | **Celery** with **Redis** broker | RQ, Dramatiq, AWS SQS | Mature; well-suited to extraction/understanding pipelines. |
| API style | **REST** + **WebSocket** for live updates | GraphQL | Simpler for v1; GraphQL only if many client variants emerge. |

## Data layer

| Concern | Choice | Alternatives considered | Why |
|---|---|---|---|
| Relational DB | **PostgreSQL 16** (managed: AWS RDS / Neon) | MySQL, CockroachDB | JSONB, trigram search, pgvector — all in one engine. |
| Object storage | **AWS S3** (or GCS / R2) | Self-hosted MinIO | Reliability, cost, lifecycle policies. |
| Cache + broker | **Redis** (managed: ElastiCache / Upstash) | Memcached, RabbitMQ | Doubles as cache and Celery broker. |
| Vector store | **pgvector** (extension on Postgres) | Pinecone, Weaviate | One database to operate; sufficient scale for Phase 1. |
| Full-text search | **Postgres** native (tsvector) | Elasticsearch, Meilisearch | Avoid second data system in Phase 1. |
| Time-series (if needed) | **TimescaleDB** (Postgres extension) | InfluxDB | Stay in Postgres. |

## ML / AI components

| Concern | Choice | Alternatives | Why |
|---|---|---|---|
| OCR (general) | **Tesseract 5** (open source) | AWS Textract, Google Document AI | Free baseline; we fall back to cloud OCR for low-confidence cases. |
| OCR (high accuracy fallback) | **AWS Textract** | Google Document AI, Azure Form Recognizer | Best-in-class for invoices and forms. |
| LLM for structured extraction | **Claude (Anthropic API)** for complex / Claude Haiku for high-volume cheap | GPT-4o, Gemini, open-source (Llama 3.1) | Strong instruction-following + JSON-mode; cost-effective with Haiku tier. |
| LLM prompt orchestration | **Instructor** (Pydantic + LLM) | LangChain, raw API | Light, predictable, schema-typed outputs. |
| Document classification | **Scikit-learn** (gradient boosting on TF-IDF features) | Fine-tuned transformer | Cheap, fast, easy to retrain weekly. |
| Anomaly detection | **PyOD** + rule-based thresholds | Custom DL | Simple, explainable, sufficient. |
| Time-series forecasting | **Prophet** (Facebook) + statsmodels fallback | NeuralProphet, custom LSTM | Reliable for monthly business cash flow patterns. |
| Vector embeddings | **OpenAI text-embedding-3-small** | Sentence-Transformers | Quality-to-cost ratio; can swap to local later. |
| Experiment tracking | **MLflow** | Weights & Biases | Self-hostable, no vendor lock-in. |

## Infrastructure

| Concern | Choice | Alternatives | Why |
|---|---|---|---|
| Cloud provider | **AWS** (primary) | GCP, Azure | Broadest service catalogue; familiar to most hires. |
| Compute (Phase 1) | **AWS ECS Fargate** (containerized services) | EKS (K8s), EC2, Cloud Run | No node management; right-size for early scale. |
| Compute (Phase 2+) | **EKS** (Kubernetes) | Stay on ECS | When we need fine-grained scaling and multi-region. |
| CI/CD | **GitHub Actions** | CircleCI, GitLab CI | Repo-native; sufficient for the workload. |
| Containers | **Docker** + multi-stage builds | — | Standard. |
| Infrastructure-as-code | **Terraform** | Pulumi, CDK | Industry standard; HCL is approachable. |
| Secrets management | **AWS Secrets Manager** | HashiCorp Vault | Native AWS integration. |
| Monitoring | **Grafana Cloud** (Prometheus + Loki + Tempo) | Datadog, New Relic | Open standards, cheaper at our stage. |
| Error tracking | **Sentry** | Rollbar, Bugsnag | Best dev experience. |
| Logging | **CloudWatch** → **Loki** (via Grafana Cloud) | — | Standard split: infra logs + app logs. |
| Email delivery | **AWS SES** + **Postmark** for transactional | SendGrid | SES cheap for bulk; Postmark for transactional reliability. |

## Frontend hosting

| Concern | Choice | Alternatives | Why |
|---|---|---|---|
| Static hosting | **Vercel** | Netlify, Cloudflare Pages | Best developer experience; previews per PR. |
| CDN | (included via Vercel / CloudFront) | — | — |

## Dev tooling

| Concern | Choice | Notes |
|---|---|---|
| Repo style | **Monorepo** (Turborepo or Nx) | One repo: `backend/`, `frontend/`, `ml-models/`, `infrastructure/`. |
| Linting | Ruff (Python), ESLint (JS/TS) | |
| Formatting | Black, Prettier | |
| Type checking | mypy (Python), tsc strict (TS) | |
| Pre-commit | Husky + lint-staged + pre-commit | |
| Testing — backend | pytest + httpx + factory-boy | |
| Testing — frontend | Vitest + React Testing Library + Playwright (E2E) | |
| API contract | OpenAPI generated from FastAPI; client TS types generated via openapi-typescript | |

## Cost ballpark (Phase 1, monthly, INR)

These are rough estimates for the first 10 customer organizations on the platform.

| Item | Estimate |
|---|---|
| AWS (compute, DB, storage, networking) | ₹25,000 – ₹40,000 |
| LLM API (Claude Haiku + small Claude Sonnet share) | ₹15,000 – ₹30,000 |
| OCR API (Textract for the 20% complex cases) | ₹5,000 – ₹10,000 |
| Vercel (frontend) | ₹2,000 |
| Monitoring (Grafana Cloud) | ₹3,000 |
| Error tracking (Sentry) | ₹2,500 |
| Email (SES + Postmark) | ₹1,500 |
| Misc tooling (GitHub, domain, Notion) | ₹5,000 |
| **Total** | **~₹60,000 – ₹95,000 / month** |

This scales sub-linearly — adding the 50th customer does not 5× the bill, especially database and infra fixed costs are amortized.

## Stack choices we are deliberately deferring

To stay focused, we are **not** introducing the following until they earn their place:

- Kubernetes (use ECS until pain demands it).
- Microservices (we run a modular monolith with clean module boundaries).
- GraphQL (REST is enough; switch later if client diversity grows).
- A separate search engine like Elasticsearch.
- A separate vector DB (pgvector inside Postgres handles v1 volume).
- Multi-region deployment.

Each one of these is a perfectly fine choice — but adding any of them before we need it will slow us down without buying anything we cannot already do.
