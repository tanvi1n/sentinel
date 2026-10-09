/**
 * api.js — Sentinel Dashboard API layer
 *
 * Wraps all backend endpoints used by the dashboard.
 * Set FIXTURE_MODE = true to run entirely from local JSON fixtures.
 * Set FIXTURE_MODE = false to connect to a live Sentinel backend.
 *
 * BASE_URL must point to the running Uvicorn server when in live mode.
 * Never place API keys or secrets here.
 */

"use strict";

// ---------------------------------------------------------------------------
// Configuration — edit these two values to switch modes
// ---------------------------------------------------------------------------

/** Set true to run from static/fixtures/ without a backend. */
const FIXTURE_MODE = true;

/** Base URL for the live backend (no trailing slash). */
const BASE_URL = "http://localhost:8000";

/** Path prefix for fixtures relative to this file's location. */
const FIXTURE_BASE = "./fixtures";

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Fetch a fixture file by name and return parsed JSON.
 * Throws a descriptive Error if the file is missing or unparseable.
 */
async function _fixture(filename) {
  const url = `${FIXTURE_BASE}/${filename}`;
  let resp;
  try {
    resp = await fetch(url);
  } catch (err) {
    throw new Error(`Fixture fetch failed (${filename}): ${err.message}`);
  }
  if (!resp.ok) {
    throw new Error(`Fixture not found: ${filename} (HTTP ${resp.status})`);
  }
  return resp.json();
}

/**
 * Perform a live fetch against the backend.
 * method  — "GET" | "POST" | "DELETE"
 * path    — e.g. "/api/scenarios"
 * body    — plain JS object (JSON-serialised) or null
 *
 * Throws a descriptive Error on network or HTTP failure.
 */
async function _live(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
  };
  if (body !== null) {
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(`${BASE_URL}${path}`, opts);
  } catch (err) {
    throw new Error(`Network error (${method} ${path}): ${err.message}`);
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const errBody = await resp.json();
      detail = errBody.detail || errBody.message || detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(`API error (${method} ${path}): ${detail}`);
  }
  // 204 No Content
  if (resp.status === 204) return null;
  return resp.json();
}

// ---------------------------------------------------------------------------
// Fixture scenario routing helpers
// ---------------------------------------------------------------------------

/**
 * Map a scenario_id to the fixture file for its first step.
 * Multi-step scenarios track their own step index in app.js; we expose
 * both step fixtures explicitly for the prompt-injection scenario.
 */
const STEP_FIXTURES = {
  "safe-action":      ["step-safe-action.json"],
  "intent-mismatch":  ["step-intent-mismatch.json"],
  "bulk-delete":      ["step-bulk-delete.json"],
  "prompt-injection": ["step-prompt-injection-1.json", "step-prompt-injection-2.json"],
};

// ---------------------------------------------------------------------------
// Public API — one exported function per backend call
// ---------------------------------------------------------------------------

/**
 * GET /api/scenarios
 * Returns array of scenario summary objects.
 */
async function apiGetScenarios() {
  if (FIXTURE_MODE) return _fixture("scenarios.json");
  return _live("GET", "/api/scenarios");
}

/**
 * GET /api/health
 * Returns { status, service, semantic_mode, semantic_provider, semantic_key_configured }.
 */
async function apiGetHealth() {
  if (FIXTURE_MODE) return _fixture("health.json");
  return _live("GET", "/api/health");
}

/**
 * POST /api/demo/start
 * @param {string} scenarioId
 * Returns { run_id, scenario_id, title, description, total_steps, current_step, status }.
 */
async function apiDemoStart(scenarioId) {
  if (FIXTURE_MODE) {
    // Return a synthetic start response derived from scenario fixtures
    const scenarios = await _fixture("scenarios.json");
    const sc = scenarios.find(s => s.id === scenarioId);
    if (!sc) throw new Error(`Unknown scenario: ${scenarioId}`);
    return {
      run_id: `run-fixture-${scenarioId}`,
      scenario_id: scenarioId,
      title: sc.title,
      description: sc.description,
      total_steps: sc.step_count,
      current_step: 0,
      status: "started",
    };
  }
  return _live("POST", "/api/demo/start", { scenario_id: scenarioId });
}

/**
 * POST /api/demo/{run_id}/step
 * @param {string} runId
 * @param {number} stepIndex  — used in fixture mode to pick the right step fixture
 * @param {string} scenarioId — used in fixture mode only
 * Returns DemoStepResponse.
 */
async function apiDemoStep(runId, stepIndex = 0, scenarioId = "") {
  if (FIXTURE_MODE) {
    const files = STEP_FIXTURES[scenarioId];
    if (!files) throw new Error(`No step fixtures for scenario: ${scenarioId}`);
    const file = files[Math.min(stepIndex, files.length - 1)];
    const data = await _fixture(file);
    // Patch in the fixture run_id so UI state stays consistent
    return { ...data, run_id: runId, step_index: stepIndex };
  }
  return _live("POST", `/api/demo/${runId}/step`);
}

/**
 * POST /api/demo/{run_id}/execute
 * @param {string} runId
 * Returns DemoExecuteResponse.
 */
async function apiDemoExecute(runId) {
  if (FIXTURE_MODE) {
    // Simulate a successful execution response
    return {
      run_id: runId,
      decision_id: `dec-fixture-exec-${Date.now()}`,
      execution_result: {
        tool: "unknown",
        success: true,
        output: { message: "[Fixture] Execution simulated successfully." },
        metadata: {},
      },
      next_step: 1,
      total_steps: 1,
      status: "completed",
    };
  }
  return _live("POST", `/api/demo/${runId}/execute`);
}

/**
 * POST /api/admin/review
 * @param {string} decisionId
 * @param {"approve"|"reject"} action
 * @param {string} reason  — required for reject, optional for approve
 * Returns updated GuardDecision.
 */
async function apiAdminReview(decisionId, action, reason = "") {
  if (FIXTURE_MODE) {
    // Return a synthetic updated decision reflecting the review outcome
    const lifecycle = action === "approve" ? "APPROVED" : "REJECTED";
    return {
      decision_id: decisionId,
      outcome: "REVIEW",
      lifecycle,
      canonical_action: {
        agent_id: "demo-agent",
        tool: "email_send",
        arguments: {},
        arg_provenance: {},
        integrity_hash: "fixture",
      },
      risk_score: 0.65,
      risk_label: "HIGH",
      violations: [],
      rules_fired: [],
      semantic_outcome: "UNSAFE",
      semantic_reason: "Intent mismatch flagged by semantic layer.",
      semantic_provider: "MOCK",
      is_reversible: false,
      evaluated_at: new Date().toISOString(),
      decided_at: new Date().toISOString(),
      executed_at: null,
      reviewer_id: "human-reviewer",
      execution_result: null,
    };
  }
  return _live("POST", "/api/admin/review", {
    decision_id: decisionId,
    action,
    reviewer_id: "human-reviewer",
    reason,
  });
}

/**
 * POST /api/admin/undo
 * @param {string} decisionId
 * Returns execution result dict.
 */
async function apiAdminUndo(decisionId) {
  if (FIXTURE_MODE) {
    return {
      tool: "file_delete",
      success: true,
      output: { message: "[Fixture] Undo simulated: file restored." },
      metadata: { undone: true },
    };
  }
  return _live("POST", "/api/admin/undo", { decision_id: decisionId });
}

/**
 * POST /api/admin/reset
 * Returns { status, message }.
 */
async function apiAdminReset() {
  if (FIXTURE_MODE) {
    return { status: "reset", message: "[Fixture] State cleared (fixture mode)." };
  }
  return _live("POST", "/api/admin/reset");
}

/**
 * GET /world
 * Returns world summary object.
 */
async function apiGetWorld() {
  if (FIXTURE_MODE) return _fixture("world-before.json");
  return _live("GET", "/world");
}

/**
 * GET /audit?n=50
 * Returns array of AuditEntry.
 */
async function apiGetAudit(n = 50) {
  if (FIXTURE_MODE) return _fixture("audit.json");
  return _live("GET", `/audit?n=${n}`);
}

/**
 * GET /review/pending
 * Returns array of GuardDecision currently pending review.
 */
async function apiGetPendingReview() {
  if (FIXTURE_MODE) return _fixture("pending-review.json");
  return _live("GET", "/review/pending");
}

/**
 * GET /decisions/{decision_id}
 * Returns a single GuardDecision.
 */
async function apiGetDecision(decisionId) {
  if (FIXTURE_MODE) {
    // Return from the first matching step fixture
    throw new Error("apiGetDecision: not needed in fixture mode");
  }
  return _live("GET", `/decisions/${decisionId}`);
}
