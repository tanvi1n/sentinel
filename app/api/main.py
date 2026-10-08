"""
api/main.py
===========
SENTINEL FastAPI application.

Routes:
  POST   /evaluate                   — evaluate an action proposal
  POST   /review/approve             — approve a REVIEW decision
  POST   /review/reject              — reject a REVIEW decision
  GET    /review/pending             — list pending review decisions
  POST   /execute                    — execute an approved decision
  POST   /undo                       — undo an executed decision
  GET    /decisions                  — list all decisions
  GET    /decisions/{decision_id}    — get a specific decision
  GET    /audit                      — get audit log
  GET    /audit/decision/{id}        — get audit for specific decision
  GET    /world                      — get world state summary
  GET    /tools                      — list registered tools
  GET    /health                     — health check
  POST   /reset                      — reset demo state

All business logic is in the service layer.
Routes are thin wrappers only.

Owned by: Person 2
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.evaluate import router as evaluate_router
from app.api.routes.review import router as review_router
from app.api.routes.execute import router as execute_router
from app.api.routes.audit import router as audit_router
from app.api.routes.admin import router as admin_router

app = FastAPI(
    title="SENTINEL Guardrail API",
    description=(
        "SENTINEL — Agentic AI Guardrail System. "
        "The agent proposes; Sentinel decides; the gateway executes."
    ),
    version="1.0.0",
)

# CORS for Person 3's frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # demo only — tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(evaluate_router, tags=["Evaluate"])
app.include_router(review_router, tags=["Review"])
app.include_router(execute_router, tags=["Execute"])
app.include_router(audit_router, tags=["Audit"])
app.include_router(admin_router, tags=["Admin"])
