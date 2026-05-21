"""Understanding layer — parsers, vendor resolution, anomaly detection.

Lives outside `api/` and `worker/` because both invoke it. The pipeline is:

    raw document  →  parsers/        →  vendors.resolve_vendor()
                                     →  anomalies.detect_for_*()
                                     →  Insight rows

All services are pure-Python (stdlib + rapidfuzz) so they're fast to test
and don't bloat the container image.
"""
