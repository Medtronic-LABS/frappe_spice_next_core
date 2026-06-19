"""
Rule-based clinical risk scoring for the Case DocType.

compute_risk() is the Observation.after_insert hook — it re-evaluates the case
after every new observation, writes risk_level + risk_factors to the Case record.

Primary path: evaluate active Recommendation Rules from the DB.
Fallback path: hardcoded NCD thresholds (bootstrap — keeps the hook working on a
fresh site before any rules are imported).
"""

import frappe

# Fallback NCD thresholds — only used when no Recommendation Rules exist.
# Concept keys must stay in sync with _VITALS_META in patient.js.
_FALLBACK_THRESHOLDS = {
    "LOINC|8480-6": {"moderate": 140.0, "high": 160.0, "label": "Elevated BP (systolic)"},
    "LOINC|8462-4": {"moderate": 90.0,  "high": 100.0, "label": "Elevated BP (diastolic)"},
    "LOINC|2339-0": {"moderate": 7.0,   "high": 11.1,  "label": "High blood glucose"},
    "LOINC|4548-4": {"moderate": 7.0,   "high": 9.0,   "label": "Poor glycaemic control (HbA1c)"},
}

_LEVELS = {"Low": 0, "Moderate": 1, "High": 2, "Critical": 3}
_LEVEL_NAMES = ["Low", "Moderate", "High"]
_SEVERITY_TO_LEVEL = {"low": 0, "moderate": 1, "high": 2, "critical": 2}


def compute_risk(doc, event):
    """doc_events hook — Observation.after_insert.
    Fetches the latest value per scored concept for this case and writes
    risk_level + risk_factors back to the Case record.
    """
    case_name = getattr(doc, "case", None)
    if not case_name:
        return

    # Build observation context from the latest value per concept for this case.
    rows = frappe.get_all(
        "Observation",
        filters={"case": case_name},
        fields=["concept", "value", "observed_dt"],
        order_by="observed_dt desc",
    )
    obs_context: dict[str, str] = {}
    for row in rows:
        if row.concept and row.concept not in obs_context:
            obs_context[row.concept] = row.value or ""

    rules = _load_recommendation_rules()
    if rules:
        level_idx, factors = _evaluate_rules(obs_context, rules)
    else:
        level_idx, factors = _evaluate_fallback(obs_context)

    frappe.db.set_value(
        "Case",
        case_name,
        {
            "risk_level": _LEVEL_NAMES[min(level_idx, 2)],
            "risk_factors": " · ".join(factors) if factors else "",
        },
        update_modified=False,
    )


def update_last_encounter_dt(doc, event):
    """doc_events hook — Encounter.after_insert.
    Keeps Case.last_encounter_dt current so the Overdue Follow-ups
    number card filter stays accurate.
    """
    case_name = getattr(doc, "case", None)
    if not case_name or not doc.encounter_dt:
        return
    frappe.db.set_value("Case", case_name, "last_encounter_dt", doc.encounter_dt, update_modified=False)


# ── Rule evaluation ───────────────────────────────────────────────────────────

def _load_recommendation_rules():
    """Return all active Recommendation Rules ordered by priority (asc)."""
    return frappe.get_all(
        "Recommendation Rule",
        filters={"active": 1},
        fields=["name", "severity", "action_label", "condition_logic"],
        order_by="priority asc",
    )


def _evaluate_rules(obs_context: dict, rules: list) -> tuple[int, list]:
    level_idx = 0
    factors = []
    for rule in rules:
        conditions = frappe.get_all(
            "Rule Condition",
            filters={"parent": rule.name, "parenttype": "Recommendation Rule"},
            fields=["condition_field", "condition_operator", "condition_value"],
        )
        if not conditions:
            continue
        logic = rule.condition_logic or "AND"
        results = [
            _eval_single(obs_context, c.condition_field, c.condition_operator, c.condition_value)
            for c in conditions
        ]
        matched = all(results) if logic == "AND" else any(results)
        if matched:
            sev = _SEVERITY_TO_LEVEL.get(rule.severity or "low", 0)
            if sev > level_idx:
                level_idx = sev
            if rule.action_label:
                factors.append(rule.action_label)
    return level_idx, factors


def _eval_single(context: dict, field: str, op: str, threshold: str) -> bool:
    raw = context.get(field)
    if raw is None:
        return False
    try:
        num = float(raw)
        thr = float(threshold)
        if op == "=":  return num == thr
        if op == "!=": return num != thr
        if op == ">":  return num > thr
        if op == ">=": return num >= thr
        if op == "<":  return num < thr
        if op == "<=": return num <= thr
    except (ValueError, TypeError):
        pass
    # String fallback
    s = str(raw).lower()
    t = str(threshold).lower()
    if op == "=":        return s == t
    if op == "!=":       return s != t
    if op == "in":       return s in [x.strip().lower() for x in t.split(",")]
    if op == "contains": return t in s
    return False


# ── Hardcoded fallback (NCD only) ─────────────────────────────────────────────

def _evaluate_fallback(obs_context: dict) -> tuple[int, list]:
    level_idx = 0
    factors = []
    for concept, thresholds in _FALLBACK_THRESHOLDS.items():
        raw = obs_context.get(concept)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (ValueError, TypeError):
            continue
        if val >= thresholds["high"]:
            level_idx = max(level_idx, 2)
            factors.append(thresholds["label"])
        elif val >= thresholds["moderate"]:
            level_idx = max(level_idx, 1)
            factors.append(thresholds["label"])
    return level_idx, factors
