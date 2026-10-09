/**
 * app.js — Sentinel Dashboard orchestrator
 *
 * Manages application state, wires UI events to api.js calls,
 * and delegates all rendering to render.js.
 *
 * State machine:
 *   IDLE → scenario selected → READY
 *   READY → Run Step → STEPPING (loading)
 *   STEPPING → decision returned → STEPPED
 *   STEPPED (APPROVE) → can execute directly
 *   STEPPED (REVIEW)  → awaiting human approve/reject
 *   STEPPED (BLOCK)   → terminal; user can only reset
 *   EXECUTING → executed → EXECUTED
 *   EXECUTED (reversible) → can undo → UNDONE
 *   Any state → Reset → IDLE
 *
 * No security logic lives here. Every decision is display-only.
 */

"use strict";

// ---------------------------------------------------------------------------
// Application state
// ---------------------------------------------------------------------------

const AppState = {
  IDLE:       "IDLE",
  READY:      "READY",
  STEPPING:   "STEPPING",
  STEPPED:    "STEPPED",
  REVIEWING:  "REVIEWING",
  EXECUTING:  "EXECUTING",
  EXECUTED:   "EXECUTED",
  UNDONE:     "UNDONE",
};

const state = {
  current:         AppState.IDLE,
  scenarios:       [],           // scenario list from API
  selectedScenario: null,        // full scenario object
  runId:           null,         // active demo run id
  stepIndex:       0,            // current step within the run
  totalSteps:      1,
  lastStepResp:    null,         // last DemoStepResponse
  lastDecision:    null,         // last GuardDecision
  pendingDecisionId: null,       // decision awaiting approve/reject/execute
  worldBefore:     null,         // world snapshot before last step
  worldAfter:      null,         // world snapshot after last execute
  hasExecuted:     false,
  undoExpected:    false,
};

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  await boot();
});

async function boot() {
  // Show fixture badge / mode in header
  renderHealth(null, FIXTURE_MODE);

  // Load health
  try {
    const health = await apiGetHealth();
    renderHealth(health, FIXTURE_MODE);
  } catch (err) {
    // Non-fatal; header will show —
    if (!FIXTURE_MODE) showStatus(`Health check failed: ${err.message}`, "warn", 5000);
  }

  // Load scenarios into selector
  try {
    const scenarios = await apiGetScenarios();
    state.scenarios = scenarios;
    renderScenarioList(document.getElementById("scenario-select"), scenarios);
  } catch (err) {
    showStatus(`Failed to load scenarios: ${err.message}`, "error", 0);
  }

  // Load initial audit log
  await refreshAuditLog();

  // Load initial review queue
  await refreshReviewQueue();

  // Load initial world state
  await refreshWorldState(/* asSnapshot */ false);

  // Wire up all events
  wireEvents();

  transition(AppState.IDLE);
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

function wireEvents() {
  // Scenario select
  const scSelect = document.getElementById("scenario-select");
  if (scSelect) {
    scSelect.addEventListener("change", onScenarioSelected);
  }

  // Run Step button
  document.getElementById("btn-run-step")
    ?.addEventListener("click", onRunStep);

  // Approve / Reject
  document.getElementById("btn-approve")
    ?.addEventListener("click", () => onReview("approve"));
  document.getElementById("btn-reject")
    ?.addEventListener("click", () => onReview("reject"));

  // Execute
  document.getElementById("btn-execute")
    ?.addEventListener("click", onExecute);

  // Undo
  document.getElementById("btn-undo")
    ?.addEventListener("click", onUndo);

  // Reset
  document.getElementById("btn-reset")
    ?.addEventListener("click", onReset);

  // Refresh buttons in right panel
  document.getElementById("btn-refresh-audit")
    ?.addEventListener("click", refreshAuditLog);
  document.getElementById("btn-refresh-review")
    ?.addEventListener("click", refreshReviewQueue);
}

// ---------------------------------------------------------------------------
// State transition — updates UI control visibility/enabled state
// ---------------------------------------------------------------------------

function transition(newState) {
  state.current = newState;
  updateControls();
}

function updateControls() {
  const s = state.current;
  const dec = state.lastDecision;
  const outcome = dec?.outcome;
  const lifecycle = dec?.lifecycle;

  // Run Step: enabled in READY; also enabled in STEPPED when there are more steps
  const moreSteps = state.stepIndex < state.totalSteps;
  setButtonEnabled("btn-run-step",
    (s === AppState.READY) ||
    (s === AppState.STEPPED && moreSteps && outcome === "APPROVE") ||
    (s === AppState.EXECUTED && moreSteps));

  // Scenario select: disabled while stepping or executing
  const scSelect = document.getElementById("scenario-select");
  if (scSelect) {
    scSelect.disabled = [AppState.STEPPING, AppState.EXECUTING].includes(s);
  }

  // Review row: visible when awaiting human decision
  const needsReview = s === AppState.STEPPED && outcome === "REVIEW"
    && lifecycle === "PENDING_REVIEW";
  showElement("review-action-row", needsReview);
  setButtonEnabled("btn-approve", needsReview);
  setButtonEnabled("btn-reject",  needsReview);

  // Execute: visible when APPROVE or lifecycle APPROVED
  const canExecute = s === AppState.STEPPED &&
    (outcome === "APPROVE" || lifecycle === "APPROVED");
  showElement("btn-execute", canExecute);
  setButtonEnabled("btn-execute", canExecute);

  // Undo: visible only after EXECUTED if reversible and undo_expected flag set
  const canUndo = s === AppState.EXECUTED && state.undoExpected && dec?.is_reversible;
  showElement("btn-undo", canUndo);
  setButtonEnabled("btn-undo", canUndo);

  // Reset: always available
  setButtonEnabled("btn-reset", true);

  // World after/diff sections: show only after execution
  showElement("world-after-section", state.hasExecuted && state.worldAfter !== null);
  showElement("world-diff-section",  state.hasExecuted && state.worldAfter !== null);
}

// ---------------------------------------------------------------------------
// Scenario selected
// ---------------------------------------------------------------------------

async function onScenarioSelected() {
  const scSelect = document.getElementById("scenario-select");
  const scenarioId = scSelect?.value;

  if (!scenarioId) {
    hideScenarioInfo();
    transition(AppState.IDLE);
    return;
  }

  const sc = state.scenarios.find(s => s.id === scenarioId);
  if (!sc) return;

  state.selectedScenario = sc;
  state.runId      = null;
  state.stepIndex  = 0;
  state.totalSteps = sc.step_count;
  state.lastDecision = null;
  state.lastStepResp = null;
  state.pendingDecisionId = null;
  state.worldBefore = null;
  state.worldAfter  = null;
  state.hasExecuted = false;
  state.undoExpected = false;

  showElement("sc-info-block", true);
  showElement("proposal-block", false);
  showElement("exec-result-section", false);

  renderScenarioInfo(sc);
  clearDecisionPanel();

  // Snapshot world before any step
  try {
    const world = await apiGetWorld();
    state.worldBefore = world;
    renderWorldState(world, null, false);
  } catch (_) { /* non-fatal */ }

  transition(AppState.READY);
  showStatus(`Scenario "${sc.title}" loaded. Press Run Step to begin.`, "info");
}

function hideScenarioInfo() {
  showElement("sc-info-block", false);
  showElement("proposal-block", false);
  clearDecisionPanel();
}

// ---------------------------------------------------------------------------
// Run Step
// ---------------------------------------------------------------------------

async function onRunStep() {
  if (!state.selectedScenario) return;

  transition(AppState.STEPPING);
  showStatus("Evaluating step…", "info", 0);
  setButtonEnabled("btn-run-step", false);

  try {
    // Start a new demo run if needed
    if (!state.runId || state.stepIndex === 0) {
      const startResp = await apiDemoStart(state.selectedScenario.id);
      state.runId = startResp.run_id;
      state.totalSteps = startResp.total_steps;
      state.stepIndex = 0;
    }

    // Evaluate step
    const stepResp = await apiDemoStep(
      state.runId,
      state.stepIndex,
      state.selectedScenario.id
    );

    state.lastStepResp   = stepResp;
    state.lastDecision   = stepResp.decision;
    state.pendingDecisionId = stepResp.decision?.decision_id || null;
    state.undoExpected   = stepResp.undo_expected || false;
    state.hasExecuted    = false;
    state.worldAfter     = null;

    // Render the decision
    renderDecision(state.lastDecision);
    renderStepProposal(stepResp);
    showElement("proposal-block", true);

    // Update world before snapshot (taken at start of this step)
    if (state.worldBefore) {
      renderWorldState(state.worldBefore, null, false);
    }

    transition(AppState.STEPPED);
    clearStatus();

    // Status message according to outcome
    const outcome = stepResp.decision?.outcome;
    if (outcome === "APPROVE") {
      showStatus("✓ APPROVE — action may run. Press Execute to proceed.", "success");
    } else if (outcome === "REVIEW") {
      showStatus("⚠ REVIEW — human decision required.", "warn", 0);
      // Switch to Review tab automatically
      switchRightTab("review");
      await refreshReviewQueue();
    } else if (outcome === "BLOCK") {
      showStatus("✗ BLOCK — action is prohibited and cannot run.", "error", 0);
    }

    // Refresh audit
    await refreshAuditLog();

  } catch (err) {
    transition(AppState.READY);
    showStatus(`Step failed: ${err.message}`, "error", 0);
  }
}

// ---------------------------------------------------------------------------
// Review (approve / reject)
// ---------------------------------------------------------------------------

async function onReview(action) {
  if (!state.pendingDecisionId) return;

  setButtonEnabled("btn-approve", false);
  setButtonEnabled("btn-reject",  false);
  showStatus(`Sending ${action}…`, "info", 0);

  try {
    const updated = await apiAdminReview(state.pendingDecisionId, action);

    state.lastDecision = updated;
    renderDecision(updated);

    if (action === "approve") {
      showStatus("✓ Approved. Press Execute to run the action.", "success");
      transition(AppState.STEPPED);
      // Force lifecycle to APPROVED so execute button appears
      // (state machine reads from state.lastDecision)
    } else {
      showStatus("✗ Rejected. No action will be taken.", "error", 5000);
      transition(AppState.STEPPED);
    }

    showElement("review-action-row", false);
    await refreshAuditLog();
    await refreshReviewQueue();

  } catch (err) {
    showStatus(`Review failed: ${err.message}`, "error", 0);
    setButtonEnabled("btn-approve", true);
    setButtonEnabled("btn-reject",  true);
  }
}

// ---------------------------------------------------------------------------
// Execute
// ---------------------------------------------------------------------------

async function onExecute() {
  if (!state.runId) return;

  transition(AppState.EXECUTING);
  showStatus("Executing…", "info", 0);
  setButtonEnabled("btn-execute", false);

  try {
    // Snapshot world before execution
    try {
      const wBefore = await apiGetWorld();
      state.worldBefore = wBefore;
    } catch (_) { /* non-fatal */ }

    const execResp = await apiDemoExecute(state.runId);

    // Snapshot world after execution
    try {
      const wAfter = await apiGetWorld();
      state.worldAfter = wAfter;
    } catch (_) { /* non-fatal */ }

    state.hasExecuted = true;
    state.stepIndex   = execResp.next_step ?? (state.stepIndex + 1);

    renderExecutionResult(execResp, false);
    renderWorldState(state.worldBefore, state.worldAfter, true);

    transition(AppState.EXECUTED);
    clearStatus();
    showStatus("⚡ Executed successfully.", "success");

    // Multi-step: prompt for next step
    if (state.stepIndex < state.totalSteps) {
      showStatus(
        `⚡ Executed. Step ${state.stepIndex}/${state.totalSteps} complete. Press Run Step for next.`,
        "success"
      );
    } else {
      showStatus("⚡ All steps complete.", "success");
    }

    await refreshAuditLog();

  } catch (err) {
    transition(AppState.STEPPED);
    showStatus(`Execute failed: ${err.message}`, "error", 0);
  }
}

// ---------------------------------------------------------------------------
// Undo
// ---------------------------------------------------------------------------

async function onUndo() {
  if (!state.pendingDecisionId) return;

  setButtonEnabled("btn-undo", false);
  showStatus("Undoing…", "info", 0);

  try {
    const undoResult = await apiAdminUndo(state.pendingDecisionId);

    // Snapshot world after undo
    try {
      const wAfter = await apiGetWorld();
      state.worldAfter = wAfter;
      renderWorldState(state.worldBefore, wAfter, true);
    } catch (_) { /* non-fatal */ }

    renderExecutionResult(undoResult, true);
    transition(AppState.UNDONE);
    showStatus("↩ Undo complete — world state restored.", "success");

    await refreshAuditLog();

  } catch (err) {
    setButtonEnabled("btn-undo", true);
    showStatus(`Undo failed: ${err.message}`, "error", 0);
  }
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

async function onReset() {
  setButtonEnabled("btn-reset", false);
  showStatus("Resetting…", "info", 0);

  try {
    await apiAdminReset();
  } catch (err) {
    // Non-fatal in fixture mode; the UI resets anyway
    if (!FIXTURE_MODE) {
      showStatus(`Reset failed: ${err.message}`, "error", 0);
      setButtonEnabled("btn-reset", true);
      return;
    }
  }

  // Reset UI state
  state.current          = AppState.IDLE;
  state.selectedScenario = null;
  state.runId            = null;
  state.stepIndex        = 0;
  state.lastDecision     = null;
  state.lastStepResp     = null;
  state.pendingDecisionId = null;
  state.worldBefore      = null;
  state.worldAfter       = null;
  state.hasExecuted      = false;
  state.undoExpected     = false;

  // Reset scenario select
  const scSelect = document.getElementById("scenario-select");
  if (scSelect) scSelect.value = "";

  hideScenarioInfo();
  clearDecisionPanel();
  showElement("exec-result-section", false);
  showElement("review-action-row",   false);
  showElement("btn-execute",         false);
  showElement("btn-undo",            false);
  renderWorldState(null, null, false);

  await refreshAuditLog();
  await refreshReviewQueue();

  transition(AppState.IDLE);
  showStatus("⟳ Reset complete. Choose a scenario to begin.", "success");
}

// ---------------------------------------------------------------------------
// Right-panel data refreshes
// ---------------------------------------------------------------------------

async function refreshAuditLog() {
  try {
    const entries = await apiGetAudit(50);
    renderAuditLog(entries);
  } catch (err) {
    // Silently fail; audit is supplementary
  }
}

async function refreshReviewQueue() {
  try {
    const pending = await apiGetPendingReview();
    renderReviewQueue(
      pending,
      (decisionId) => _reviewFromQueue(decisionId, "approve"),
      (decisionId) => _reviewFromQueue(decisionId, "reject")
    );
  } catch (err) {
    // Silently fail
  }
}

/** Handle approve/reject triggered from the right-panel review queue. */
async function _reviewFromQueue(decisionId, action) {
  showStatus(`Sending ${action} for ${decisionId.substring(0, 20)}…`, "info", 0);
  try {
    const updated = await apiAdminReview(decisionId, action, "");

    // If this is the current decision, update the centre panel
    if (decisionId === state.pendingDecisionId) {
      state.lastDecision = updated;
      renderDecision(updated);
      showElement("review-action-row", false);
      if (action === "approve") {
        transition(AppState.STEPPED);
        showStatus("✓ Approved. Press Execute to run the action.", "success");
      } else {
        transition(AppState.STEPPED);
        showStatus("✗ Rejected. No action will be taken.", "error", 5000);
      }
    }

    await refreshReviewQueue();
    await refreshAuditLog();
  } catch (err) {
    showStatus(`Review failed: ${err.message}`, "error", 0);
  }
}

async function refreshWorldState(asSnapshot) {
  try {
    const world = await apiGetWorld();
    if (asSnapshot) {
      state.worldBefore = world;
    }
    renderWorldState(state.worldBefore || world, state.worldAfter, state.hasExecuted);
  } catch (_) { /* non-fatal */ }
}
