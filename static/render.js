/**
 * render.js — Sentinel Dashboard rendering layer
 *
 * Pure DOM-manipulation functions. Each function receives backend data
 * and updates the document. No fetch calls, no state, no security logic.
 *
 * All untrusted strings from the backend are inserted as textContent,
 * never as innerHTML, to prevent XSS.
 */

"use strict";

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

/** Safely set element text. Does nothing if el is null. */
function setText(el, text) {
  if (el) el.textContent = (text === null || text === undefined) ? "—" : String(text);
}

/** Safely show/hide an element. */
function setVisible(el, visible) {
  if (el) el.style.display = visible ? "" : "none";
}

/** Add or remove a CSS class. */
function setClass(el, cls, active) {
  if (!el) return;
  if (active) el.classList.add(cls);
  else el.classList.remove(cls);
}

/** Create a DOM element with optional className and text. */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

/** Format an ISO timestamp to a readable local time string. */
function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch (_) { return iso; }
}

/** Format a float to 2 decimal places. */
function fmtScore(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toFixed(2);
}

/** Return a CSS class suffix for an outcome string. */
function outcomeClass(outcome) {
  switch ((outcome || "").toUpperCase()) {
    case "APPROVE": return "approve";
    case "REVIEW":  return "review";
    case "BLOCK":   return "block";
    default:        return "unknown";
  }
}

/** Return a CSS class suffix for a semantic outcome string. */
function semanticClass(outcome) {
  switch ((outcome || "").toUpperCase()) {
    case "SAFE":        return "safe";
    case "SUSPICIOUS":  return "suspicious";
    case "UNSAFE":      return "unsafe";
    case "UNAVAILABLE":
    case "TIMEOUT":
    case "INVALID":     return "unavailable";
    default:            return "unknown";
  }
}

/** Return a CSS class for risk label. */
function riskClass(label) {
  switch ((label || "").toUpperCase()) {
    case "LOW":      return "risk-low";
    case "MEDIUM":   return "risk-medium";
    case "HIGH":     return "risk-high";
    case "CRITICAL": return "risk-critical";
    default:         return "";
  }
}

/** Return a human-readable provenance label. */
function provenanceLabel(prov) {
  switch ((prov || "").toLowerCase()) {
    case "user_task":        return "User task";
    case "agent_internal":   return "Agent internal";
    case "external_content": return "⚠ External content";
    case "system":           return "System";
    default:                 return prov || "—";
  }
}

/** Return CSS class for a provenance value. */
function provenanceClass(prov) {
  if ((prov || "").toLowerCase() === "external_content") return "prov-external";
  if ((prov || "").toLowerCase() === "user_task") return "prov-user";
  return "prov-internal";
}

// ---------------------------------------------------------------------------
// Scenario selector rendering
// ---------------------------------------------------------------------------

/**
 * Populate the scenario <select> element.
 * @param {HTMLSelectElement} selectEl
 * @param {Array} scenarios  — from GET /api/scenarios
 */
function renderScenarioList(selectEl, scenarios) {
  if (!selectEl) return;
  // Keep the placeholder option
  while (selectEl.options.length > 1) selectEl.remove(1);
  (scenarios || []).forEach(sc => {
    const opt = document.createElement("option");
    opt.value = sc.id;
    opt.textContent = `${sc.title} (${sc.expected_verdict})`;
    selectEl.appendChild(opt);
  });
}

/**
 * Render scenario metadata into the left-panel info block.
 * @param {Object} sc — one entry from the scenarios list
 */
function renderScenarioInfo(sc) {
  setText(document.getElementById("sc-title"),       sc?.title);
  setText(document.getElementById("sc-description"), sc?.description);
  setText(document.getElementById("sc-user-task"),   sc?.user_task || "—");
  setText(document.getElementById("sc-expected"),    sc?.expected_verdict || "—");
}

/**
 * Render the current step proposal info in the left panel.
 * @param {Object} stepResp — DemoStepResponse from POST /api/demo/{run_id}/step
 */
function renderStepProposal(stepResp) {
  const d = stepResp?.decision;
  const ca = d?.canonical_action;

  const stepText = `Step ${(stepResp?.step_index ?? 0) + 1} / ${stepResp?.total_steps ?? "?"}`;
  setText(document.getElementById("step-current"),       stepText);
  setText(document.getElementById("step-current-legacy"), stepText);

  setText(document.getElementById("prop-tool"),  ca?.tool || "—");
  setText(document.getElementById("prop-agent"), ca?.agent_id || "—");

  const argsEl = document.getElementById("prop-args");
  if (argsEl) {
    const args = ca?.arguments;
    if (!args || Object.keys(args).length === 0) {
      argsEl.textContent = "(none)";
    } else {
      argsEl.textContent = JSON.stringify(args, null, 2);
    }
  }
}

// ---------------------------------------------------------------------------
// Health bar rendering
// ---------------------------------------------------------------------------

/**
 * Render backend health status into the header status bar.
 * @param {Object} health — from GET /api/health
 * @param {boolean} fixtureMode
 */
function renderHealth(health, fixtureMode) {
  const bar = document.getElementById("health-bar");
  if (!bar) return;

  const statusEl    = document.getElementById("health-status");
  const modeEl      = document.getElementById("health-mode");
  const providerEl  = document.getElementById("health-provider");
  const keyEl       = document.getElementById("health-key");
  const fixtureBadge = document.getElementById("fixture-badge");

  setText(statusEl,   health?.status || "—");
  setText(modeEl,     health?.semantic_mode || "—");
  setText(providerEl, health?.semantic_provider || "—");
  setText(keyEl,      health?.semantic_key_configured || "—");

  setClass(bar, "health-ok",  health?.status === "ok");
  setClass(bar, "health-err", health?.status !== "ok");

  if (fixtureBadge) {
    fixtureBadge.style.display = fixtureMode ? "inline-flex" : "none";
  }
}

// ---------------------------------------------------------------------------
// Centre panel — GuardDecision rendering
// ---------------------------------------------------------------------------

/**
 * Render the full GuardDecision into the centre panel.
 * @param {Object} decision — GuardDecision from the backend
 */
function renderDecision(decision) {
  if (!decision) {
    clearDecisionPanel();
    return;
  }

  const ca = decision.canonical_action || {};

  // --- Verdict badge ---
  const verdictEl = document.getElementById("verdict-badge");
  const verdictSection = document.getElementById("verdict-section");
  if (verdictEl) {
    const symbols = { APPROVE: "✓ ", REVIEW: "⚠ ", BLOCK: "✗ " };
    const sym = symbols[(decision.outcome || "").toUpperCase()] || "";
    verdictEl.textContent = sym + (decision.outcome || "—");
    verdictEl.className = `verdict-badge verdict-${outcomeClass(decision.outcome)}`;
  }
  if (verdictSection) {
    verdictSection.classList.remove("vs-approve", "vs-review", "vs-block");
    if (decision.outcome) {
      verdictSection.classList.add(`vs-${outcomeClass(decision.outcome)}`);
    }
  }

  // --- Lifecycle + IDs ---
  setText(document.getElementById("decision-id"),        decision.decision_id);
  setText(document.getElementById("decision-lifecycle"), decision.lifecycle);

  // --- Canonical action ---
  setText(document.getElementById("ca-tool"),   ca.tool);
  setText(document.getElementById("ca-agent"),  ca.agent_id);
  setText(document.getElementById("ca-hash"),   ca.integrity_hash
    ? ca.integrity_hash.substring(0, 16) + "…"
    : "—");

  const caArgsEl = document.getElementById("ca-args");
  if (caArgsEl) {
    const args = ca.arguments;
    caArgsEl.textContent = (!args || Object.keys(args).length === 0)
      ? "(none)"
      : JSON.stringify(args, null, 2);
  }

  // --- Timestamps ---
  setText(document.getElementById("ts-evaluated"), fmtTime(decision.evaluated_at));
  setText(document.getElementById("ts-decided"),   fmtTime(decision.decided_at));
  setText(document.getElementById("ts-executed"),  fmtTime(decision.executed_at));
  setText(document.getElementById("ts-reviewer"),  decision.reviewer_id || "—");

  // --- Reversibility ---
  const revEl = document.getElementById("decision-reversible");
  if (revEl) {
    revEl.textContent = decision.is_reversible ? "Yes" : "No";
    revEl.className = decision.is_reversible ? "badge badge-yes" : "badge badge-no";
  }

  // --- Four diagnostic axes ---
  _renderAxisCapability(decision);
  _renderAxisRisk(decision);
  _renderAxisProvenance(ca);
  _renderAxisSemantic(decision);

  // --- Policy violations ---
  _renderViolations(decision.violations || []);

  // --- Rules fired ---
  _renderRulesFired(decision.rules_fired || []);

  // --- Explanation text ---
  _renderExplanation(decision);
}

/** Clear the centre panel to an empty/waiting state. */
function clearDecisionPanel() {
  const verdictEl = document.getElementById("verdict-badge");
  if (verdictEl) {
    verdictEl.textContent = "—";
    verdictEl.className = "verdict-badge verdict-unknown";
  }
  const verdictSection = document.getElementById("verdict-section");
  if (verdictSection) {
    verdictSection.classList.remove("vs-approve", "vs-review", "vs-block");
  }
  ["decision-id", "decision-lifecycle", "ca-tool", "ca-agent",
   "ca-hash", "ca-args", "ts-evaluated", "ts-decided",
   "ts-executed", "ts-reviewer"].forEach(id => setText(document.getElementById(id), "—"));

  const revEl = document.getElementById("decision-reversible");
  if (revEl) { revEl.textContent = "—"; revEl.className = "badge"; }

  const violList = document.getElementById("violations-list");
  if (violList) violList.innerHTML = "";
  setText(document.getElementById("violations-empty"), "No violations.");
  setVisible(document.getElementById("violations-empty"), true);

  const rfList = document.getElementById("rules-fired-list");
  if (rfList) rfList.innerHTML = "";

  setText(document.getElementById("explanation-text"), "Run a scenario step to see the decision.");
  _clearAxes();
}

function _clearAxes() {
  ["axis-capability", "axis-risk", "axis-provenance", "axis-semantic"].forEach(id => {
    const ax = document.getElementById(id);
    if (ax) ax.innerHTML = '<span class="axis-placeholder">Not available</span>';
  });
}

// --- Capability axis ---
function _renderAxisCapability(decision) {
  const axEl = document.getElementById("axis-capability");
  if (!axEl) return;
  axEl.innerHTML = "";

  const capViols = (decision.violations || []).filter(
    v => v.rule_id === "UNAUTHORIZED_CAPABILITY" || v.rule_id === "UNKNOWN_TOOL"
  );
  if (capViols.length > 0) {
    capViols.forEach(v => {
      const row = el("div", "axis-row axis-row--fail");
      row.appendChild(el("span", "axis-icon", "✗"));
      row.appendChild(el("span", "axis-label", v.rule_id));
      row.appendChild(el("span", "axis-detail", v.message));
      axEl.appendChild(row);
    });
  } else {
    const row = el("div", "axis-row axis-row--ok");
    row.appendChild(el("span", "axis-icon", "✓"));
    row.appendChild(el("span", "axis-label", "Authorized"));
    row.appendChild(el("span", "axis-detail",
      `Tool: ${decision.canonical_action?.tool || "—"}`));
    axEl.appendChild(row);
  }
}

// --- Risk axis ---
function _renderAxisRisk(decision) {
  const axEl = document.getElementById("axis-risk");
  if (!axEl) return;
  axEl.innerHTML = "";

  const label = decision.risk_label || "—";
  const score = decision.risk_score;
  const cls = riskClass(label);

  const row = el("div", `axis-row ${cls}`);

  const barWrap = el("div", "risk-bar-wrap");
  const barFill = el("div", "risk-bar-fill");
  barFill.style.width = `${Math.round((score || 0) * 100)}%`;
  barFill.className = `risk-bar-fill ${cls}`;
  barWrap.appendChild(barFill);

  row.appendChild(el("span", "axis-label", label));
  row.appendChild(el("span", "axis-score", fmtScore(score)));
  row.appendChild(barWrap);
  axEl.appendChild(row);
}

// --- Provenance axis ---
function _renderAxisProvenance(ca) {
  const axEl = document.getElementById("axis-provenance");
  if (!axEl) return;
  axEl.innerHTML = "";

  const prov = ca?.arg_provenance;
  if (!prov || Object.keys(prov).length === 0) {
    axEl.appendChild(el("span", "axis-placeholder", "No provenance data available."));
    return;
  }

  Object.entries(prov).forEach(([argName, provValue]) => {
    const row = el("div", `axis-row`);
    const nameEl = el("span", "prov-arg-name", `${argName}:`);
    const valEl  = el("span", `prov-value ${provenanceClass(provValue)}`,
                      provenanceLabel(provValue));
    row.appendChild(nameEl);
    row.appendChild(valEl);
    axEl.appendChild(row);
  });
}

// --- Semantic axis ---
function _renderAxisSemantic(decision) {
  const axEl = document.getElementById("axis-semantic");
  if (!axEl) return;
  axEl.innerHTML = "";

  const outcome  = decision.semantic_outcome;
  const reason   = decision.semantic_reason;
  const provider = decision.semantic_provider;

  if (!outcome) {
    axEl.appendChild(el("span", "axis-placeholder", "No semantic check for this tool."));
    return;
  }

  const headerRow = el("div", "axis-row");
  headerRow.appendChild(el("span", `sem-badge sem-${semanticClass(outcome)}`, outcome));
  if (provider) {
    headerRow.appendChild(el("span", "sem-provider", `via ${provider}`));
  }
  axEl.appendChild(headerRow);

  if (reason) {
    const reasonRow = el("div", "sem-reason");
    reasonRow.textContent = reason;
    axEl.appendChild(reasonRow);
  }

  if (decision.outcome === "BLOCK") {
    const noteRow = el("div", "sem-advisory-note",
      "ⓘ Semantic result is advisory — it cannot override a deterministic BLOCK.");
    axEl.appendChild(noteRow);
  }
}

// --- Violations list ---
function _renderViolations(violations) {
  const listEl  = document.getElementById("violations-list");
  const emptyEl = document.getElementById("violations-empty");
  if (!listEl) return;

  listEl.innerHTML = "";
  if (violations.length === 0) {
    setVisible(emptyEl, true);
    return;
  }
  setVisible(emptyEl, false);

  violations.forEach(v => {
    const item = el("li", `violation-item sev-${(v.severity || "").toLowerCase()}`);
    const header = el("div", "violation-header");
    header.appendChild(el("code", "rule-id", v.rule_id));
    header.appendChild(el("span", `sev-badge`, v.severity || ""));
    const msg = el("div", "violation-msg", v.message);
    item.appendChild(header);
    item.appendChild(msg);
    listEl.appendChild(item);
  });
}

// --- Rules fired ---
function _renderRulesFired(rules) {
  const listEl = document.getElementById("rules-fired-list");
  if (!listEl) return;
  listEl.innerHTML = "";

  if (rules.length === 0) {
    listEl.appendChild(el("span", "axis-placeholder", "No rules fired."));
    return;
  }
  rules.forEach(r => {
    const chip = el("span", "rule-chip", r);
    listEl.appendChild(chip);
  });
}

// --- Explanation text ---
function _renderExplanation(decision) {
  const expEl = document.getElementById("explanation-text");
  if (!expEl) return;

  const lines = [];
  const out = decision.outcome;

  const headlines = {
    APPROVE: "APPROVE — The action may run.",
    REVIEW:  "REVIEW — The action requires human approval before it can run.",
    BLOCK:   "BLOCK — The action is prohibited and cannot run.",
  };
  lines.push(headlines[out] || `Outcome: ${out}`);
  lines.push("");

  const ca = decision.canonical_action || {};
  lines.push(`Tool: ${ca.tool || "—"} (agent: ${ca.agent_id || "—"})`);
  const args = ca.arguments;
  lines.push(`Arguments: ${(!args || Object.keys(args).length === 0)
    ? "(none)" : JSON.stringify(args)}`);
  lines.push(`Risk: ${decision.risk_label || "—"} (${fmtScore(decision.risk_score)}). ` +
    `Reversible: ${decision.is_reversible ? "yes" : "no"}.`);
  lines.push("");

  if ((decision.violations || []).length > 0) {
    lines.push("Policy findings:");
    decision.violations.forEach(v => {
      lines.push(`  • ${v.rule_id} [${v.severity}]: ${v.message}`);
    });
  } else {
    lines.push("Policy findings: none.");
  }
  lines.push("");

  // Provenance
  const prov = ca.arg_provenance || {};
  const external = Object.entries(prov).filter(([, p]) => p === "external_content");
  if (external.length > 0) {
    lines.push("Argument provenance (set by harness, not by agent):");
    external.forEach(([name]) => {
      lines.push(`  • "${name}" — originated from external (untrusted) content`);
    });
    lines.push("");
  }

  // Semantic
  const semOut = decision.semantic_outcome;
  if (!semOut) {
    lines.push("Semantic check: not required for this tool.");
  } else {
    const prov2 = decision.semantic_provider || "unknown provider";
    const failStates = ["INVALID", "TIMEOUT", "UNAVAILABLE"];
    if (failStates.includes((semOut || "").toUpperCase())) {
      lines.push(`Semantic check (advisory, ${prov2}): unavailable (${semOut}). No finding produced.`);
    } else {
      lines.push(`Semantic check (advisory, ${prov2}): ${semOut}.`);
      if (decision.semantic_reason) {
        lines.push(`  Model rationale: "${decision.semantic_reason}"`);
      }
    }
    if (out === "BLOCK") {
      lines.push("Semantic findings are advisory and did not affect this block.");
    }
  }

  lines.push("");
  const nexts = {
    APPROVE: "Next: the action may run.",
    REVIEW:  "Next: a human reviewer must approve or reject. Execution requires that approval.",
    BLOCK:   "Next: execution is prohibited. A reviewer cannot override this block.",
  };
  lines.push(nexts[out] || "");

  expEl.textContent = lines.join("\n");
}

// ---------------------------------------------------------------------------
// Review queue rendering (right panel)
// ---------------------------------------------------------------------------

/**
 * Render the pending review queue.
 * @param {Array}    decisions     — from GET /review/pending
 * @param {Function} onApprove     — callback(decision_id)
 * @param {Function} onReject      — callback(decision_id)
 */
function renderReviewQueue(decisions, onApprove, onReject) {
  const listEl  = document.getElementById("review-queue-list");
  const emptyEl = document.getElementById("review-queue-empty");
  if (!listEl) return;

  listEl.innerHTML = "";
  if (!decisions || decisions.length === 0) {
    setVisible(emptyEl, true);
    return;
  }
  setVisible(emptyEl, false);

  decisions.forEach(d => {
    const item = el("div", "review-item");

    const hdr = el("div", "review-item-header");
    hdr.appendChild(el("code", "review-tool", d.canonical_action?.tool || "—"));
    hdr.appendChild(el("span", `sev-badge`, d.risk_label || "—"));
    item.appendChild(hdr);

    const idRow = el("div", "review-item-id");
    idRow.appendChild(el("span", "", "ID: "));
    idRow.appendChild(el("code", "", d.decision_id?.substring(0, 20) + "…"));
    item.appendChild(idRow);

    if (d.semantic_outcome) {
      item.appendChild(el("div", `review-item-sem sem-${semanticClass(d.semantic_outcome)}`,
        `Semantic: ${d.semantic_outcome} (${d.semantic_provider || "—"})`));
    }

    const actions = el("div", "review-item-actions");

    const approveBtn = el("button", "btn btn-approve", "Approve");
    approveBtn.type = "button";
    approveBtn.setAttribute("aria-label", `Approve decision ${d.decision_id}`);
    approveBtn.addEventListener("click", () => onApprove(d.decision_id));

    const rejectBtn = el("button", "btn btn-reject", "Reject");
    rejectBtn.type = "button";
    rejectBtn.setAttribute("aria-label", `Reject decision ${d.decision_id}`);
    rejectBtn.addEventListener("click", () => onReject(d.decision_id));

    actions.appendChild(approveBtn);
    actions.appendChild(rejectBtn);
    item.appendChild(actions);

    listEl.appendChild(item);
  });
}

// ---------------------------------------------------------------------------
// World state rendering (right panel)
// ---------------------------------------------------------------------------

/**
 * Render the world state comparison.
 * @param {Object|null} before — world summary snapshot taken before a step
 * @param {Object|null} after  — current world summary
 * @param {boolean}     hasExecuted — whether an execution actually occurred
 */
function renderWorldState(before, after, hasExecuted) {
  const beforeEl      = document.getElementById("world-before");
  const afterEl       = document.getElementById("world-after");
  const diffEl        = document.getElementById("world-diff");
  const afterSection  = document.getElementById("world-after-section");
  const diffSection   = document.getElementById("world-diff-section");

  // Show current/before summary
  if (beforeEl) {
    beforeEl.innerHTML = "";
    _appendWorldSummary(beforeEl, before, "Before");
  }

  if (!hasExecuted || !after) {
    if (afterEl) { afterEl.innerHTML = ""; afterEl.appendChild(el("span", "world-placeholder", "Run a step to see world changes.")); }
    if (diffEl)  { diffEl.innerHTML  = ""; }
    setVisible(afterSection, false);
    setVisible(diffSection,  false);
    return;
  }

  setVisible(afterSection, true);
  setVisible(diffSection,  true);

  if (afterEl) {
    afterEl.innerHTML = "";
    _appendWorldSummary(afterEl, after, "After");
  }

  if (diffEl) {
    diffEl.innerHTML = "";
    const changes = _computeWorldDiff(before, after);
    if (changes.length === 0) {
      diffEl.appendChild(el("span", "world-placeholder", "No observable change in world summary."));
    } else {
      const noteEl = el("p", "world-diff-note",
        "World summary comparison (frontend snapshot — not a server-generated diff):");
      diffEl.appendChild(noteEl);
      changes.forEach(c => {
        const row = el("div", `world-diff-row world-diff-${c.type}`);
        row.appendChild(el("span", "diff-field", c.field));
        row.appendChild(el("span", "diff-from",  String(c.before)));
        row.appendChild(el("span", "diff-arrow",  "→"));
        row.appendChild(el("span", "diff-to",    String(c.after)));
        diffEl.appendChild(row);
      });
    }
  }
}

function _appendWorldSummary(container, world, label) {
  if (!world) {
    container.appendChild(el("span", "world-placeholder", `${label}: not available.`));
    return;
  }
  const title = el("div", "world-label", label);
  container.appendChild(title);

  const table = el("table", "world-table");
  const tbody = el("tbody", "");
  table.appendChild(tbody);

  const fields = [
    ["Emails in inbox",  world.emails_in_inbox],
    ["Emails sent",      world.emails_sent],
    ["Calendar events",  world.calendar_events],
    ["Files",            world.files],
  ];
  fields.forEach(([name, val]) => {
    const tr = el("tr", "");
    tr.appendChild(el("td", "", name));
    tr.appendChild(el("td", "", val ?? "—"));
    tbody.appendChild(tr);
  });

  // Payment accounts
  const accounts = world.payment_accounts;
  if (accounts && typeof accounts === "object") {
    Object.entries(accounts).forEach(([acct, info]) => {
      const tr = el("tr", "");
      tr.appendChild(el("td", "", `${acct} balance`));
      tr.appendChild(el("td", "",
        `${info.currency || ""} ${Number(info.balance || 0).toLocaleString()}`));
      tbody.appendChild(tr);
    });
  }

  container.appendChild(table);
}

function _computeWorldDiff(before, after) {
  if (!before || !after) return [];
  const changes = [];
  const simpleFields = ["emails_in_inbox", "emails_sent", "calendar_events", "files"];
  simpleFields.forEach(f => {
    if (before[f] !== after[f]) {
      changes.push({ field: f, before: before[f], after: after[f],
        type: after[f] > before[f] ? "added" : "removed" });
    }
  });
  return changes;
}

// ---------------------------------------------------------------------------
// Audit log rendering (right panel)
// ---------------------------------------------------------------------------

/**
 * Render audit log entries.
 * @param {Array} entries — from GET /audit
 */
function renderAuditLog(entries) {
  const listEl  = document.getElementById("audit-list");
  const emptyEl = document.getElementById("audit-empty");
  if (!listEl) return;

  listEl.innerHTML = "";
  if (!entries || entries.length === 0) {
    setVisible(emptyEl, true);
    return;
  }
  setVisible(emptyEl, false);

  // Show most recent first (backend sends oldest-first in some modes)
  const sorted = [...entries].sort((a, b) =>
    new Date(b.timestamp) - new Date(a.timestamp));

  sorted.forEach(entry => {
    const evClass = `ev-${(entry.event || "").toLowerCase().replace(/_/g, "-")}`;
    const item = el("div", `audit-item ${evClass}`);

    // Left accent stripe
    item.appendChild(el("div", "audit-stripe", ""));

    // Content wrapper
    const content = el("div", "audit-content");

    const hdr = el("div", "audit-item-header");
    hdr.appendChild(el("span",
      `audit-event audit-event-${(entry.event || "").toLowerCase().replace(/_/g, "-")}`,
      entry.event || "—"));
    if (entry.tool)    hdr.appendChild(el("code", "audit-tool", entry.tool));
    if (entry.outcome) hdr.appendChild(el("span",
      `audit-outcome audit-outcome-${outcomeClass(entry.outcome)}`, entry.outcome));
    content.appendChild(hdr);

    const meta = el("div", "audit-item-meta");
    meta.appendChild(el("span", "audit-ts", fmtTime(entry.timestamp)));
    if (entry.latency_ms !== null && entry.latency_ms !== undefined) {
      meta.appendChild(el("span", "audit-latency", `${Number(entry.latency_ms).toFixed(1)} ms`));
    }
    content.appendChild(meta);

    if (entry.message) {
      content.appendChild(el("div", "audit-msg", entry.message));
    }

    item.appendChild(content);
    listEl.appendChild(item);
  });
}

// ---------------------------------------------------------------------------
// Execution result rendering
// ---------------------------------------------------------------------------

/**
 * Render an execution result into the execution-result section.
 * @param {Object} execResp — DemoExecuteResponse or raw execution result
 * @param {boolean} isUndo
 */
function renderExecutionResult(execResp, isUndo = false) {
  const secEl = document.getElementById("exec-result-section");
  const bodyEl = document.getElementById("exec-result-body");
  if (!secEl || !bodyEl) return;

  setVisible(secEl, true);
  bodyEl.textContent = "";

  const result = execResp?.execution_result || execResp;
  if (!result) {
    bodyEl.textContent = isUndo ? "Undo completed." : "Execution completed.";
    return;
  }

  const lines = [];
  lines.push(isUndo ? "=== UNDO RESULT ===" : "=== EXECUTION RESULT ===");
  lines.push(`Tool:    ${result.tool || "—"}`);
  lines.push(`Success: ${result.success}`);
  if (result.output) {
    if (typeof result.output === "object") {
      lines.push(`Output:  ${JSON.stringify(result.output, null, 2)}`);
    } else {
      lines.push(`Output:  ${result.output}`);
    }
  }
  if (execResp?.status) lines.push(`Status:  ${execResp.status}`);
  bodyEl.textContent = lines.join("\n");
}

// ---------------------------------------------------------------------------
// Status / toast notifications
// ---------------------------------------------------------------------------

/**
 * Show a transient status message in the status bar.
 * @param {string} msg
 * @param {"info"|"success"|"error"|"warn"} level
 * @param {number} timeoutMs — 0 means persistent
 */
function showStatus(msg, level = "info", timeoutMs = 4000) {
  const barEl = document.getElementById("status-bar");
  if (!barEl) return;

  barEl.textContent = msg;
  barEl.className = `status-bar status-${level}`;
  barEl.style.display = "block";

  if (timeoutMs > 0) {
    setTimeout(() => {
      if (barEl.textContent === msg) barEl.style.display = "none";
    }, timeoutMs);
  }
}

/** Clear the status bar. */
function clearStatus() {
  const barEl = document.getElementById("status-bar");
  if (barEl) barEl.style.display = "none";
}

// ---------------------------------------------------------------------------
// Control state helpers
// ---------------------------------------------------------------------------

/**
 * Enable or disable a button by its element ID.
 * Also sets aria-disabled for accessibility.
 */
function setButtonEnabled(id, enabled) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.disabled = !enabled;
  btn.setAttribute("aria-disabled", String(!enabled));
}

/**
 * Show or hide a button/section by element ID.
 */
function showElement(id, visible) {
  setVisible(document.getElementById(id), visible);
}
