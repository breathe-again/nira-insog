# backend/ingestion/

The front door for documents. Responsible for:

- Accepting uploads (HTTP, email-to-inbox, future bank API).
- File type detection and safety checks (virus scan).
- Storing raw files in object storage (S3).
- Creating the initial `Document` record.
- Pushing the extraction job onto the Celery queue.
