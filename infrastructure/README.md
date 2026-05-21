# infrastructure/

All infrastructure-as-code and deployment configuration.

- `docker/` — Dockerfiles for each service (api, workers, frontend dev) and `docker-compose.yml` for local development.
- `deploy/` — Terraform modules for AWS resources (VPC, RDS, S3, ECS, Redis, Secrets Manager) and GitHub Actions deploy workflows.

Environments:

- `dev` — single-instance, shared by team.
- `staging` — mirrors prod topology; pre-release validation.
- `prod` — customer-facing.
