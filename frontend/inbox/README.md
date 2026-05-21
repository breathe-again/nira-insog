# frontend/inbox/

The document inbox UI.

Views:

- **List** — every uploaded document with status (`received` → `extracted` → `understood` → `indexed` → `error`). Filterable by status, type, date, uploader.
- **Detail** — original file on the left (PDF/image preview), extracted fields on the right (editable). Every edit fires a feedback event.
- **Upload** — drag-and-drop zone supporting multi-file upload with real-time progress.
- **Bulk re-process** — admin action for documents stuck in error state.

WebSocket-driven: status updates flow in live without page refresh.
