"""``HEAD /health`` and ``HEAD /healthz``.

The core serves them as GET only, so ``curl -I https://<domain>/healthz`` (the smoke check of the
shared-server runbook) got ``405``. Liveness only — same semantics as the core GET.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter(tags=["Health"])


@router.head("/health", include_in_schema=False)
@router.head("/healthz", include_in_schema=False)
async def health_head() -> Response:
    return Response(status_code=200)
