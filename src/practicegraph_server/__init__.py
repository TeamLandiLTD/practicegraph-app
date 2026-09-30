"""PracticeGraph passive backend (INV-2, FR-API).

The server only: serves versioned catalog artifacts, accepts schema-validated
anonymous aggregates, stores them, serves k-anonymous aggregate views, and
answers an unauthenticated ``/healthz`` liveness probe (no org-specific data).
It never receives, requests, or derives raw content or identity. Stdlib only —
the supply chain is the Python runtime (NFR-SEC-4).
"""

SERVER_VERSION = "0.1.0"
# CATALOG_VERSION (the frozen "bundled-2026-07-01" constant) was removed on
# 2026-08-13: the endpoint short-circuits its catalog pull on version
# equality, so a constant meant no publish ever propagated. The coordination
# version is now a content hash over the served artifacts (app.py).
