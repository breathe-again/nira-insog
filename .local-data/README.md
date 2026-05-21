# .local-data/

This folder holds persistent state for your local development environment.

Nothing in here is committed to git — it's per-developer data that should survive across `make down` / `make up` and be easy to back up.

Subfolders:

| Path | What it is |
|---|---|
| `uploads/` | Every file uploaded through the Inbox lives here, organized by `<org_id>/<hash>.ext`. The API + worker containers bind-mount this. |
| `backups/` | Timestamped DB snapshots created by `make backup-db` or `make snapshot`. |

Backup commands (run from the repo root):

```bash
make snapshot       # one timestamped backup containing DB + uploads
make backup-db      # just the database, gzipped
make restore-db FILE=.local-data/backups/postgres-YYYYMMDD-HHMMSS.sql.gz
make list-backups   # see what backups exist
```

**Important:** if you run `make clean`, it removes Docker volumes — but anything in `.local-data/` (which is bind-mounted from your Mac filesystem) is **not** touched. So uploads here survive `make clean`. The Postgres database, however, still lives in a Docker volume — back it up before `make clean` if you want to keep it.
