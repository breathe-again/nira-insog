# infrastructure/deploy/

Terraform modules and CI/CD configuration for deploying Nira Insig to AWS.

Planned structure:

- `terraform/`
  - `modules/` — reusable modules (`networking`, `database`, `ecs-service`, `redis`, `s3-bucket`).
  - `envs/dev/`, `envs/staging/`, `envs/prod/` — environment-specific compositions.
- `github-actions/` — workflow YAML for build, test, deploy.

Principles:

- Every environment is reproducible from code in this folder.
- State is stored in S3 with DynamoDB locking.
- Secrets never live in Terraform state — they live in AWS Secrets Manager and are referenced by ARN.
- Promotion path: dev → staging → prod, gated by manual approval at staging→prod.
