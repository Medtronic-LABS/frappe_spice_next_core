app_name = "uhis_next_core"
app_title = "UHIS Next Core"
app_publisher = "Medtronic Labs"
app_description = "Multi-country public health platform — server of record and sync API provider."
app_email = "admin@medtroniclabs.org"
app_license = "MIT"
app_version = "0.1.0"

# Clinical theme — loaded after frappe_theme so it takes precedence.
app_include_css = ["/assets/uhis_next_core/css/uhis_theme.css"]

# Shared AI panel utilities (typewriter, chat, highlight).
app_include_js = ["/assets/uhis_next_core/js/ai_panel.js"]

# Fixtures committed to git; bench export-fixtures writes these files.
# Order matters: Number Card must precede Workspace (workspace references card names).
fixtures = [
    "Concept",
    "Clinical Question",
    {
        "dt": "Property Setter",
        "filters": [["doc_type", "in", [
            "Patient", "Case", "Household", "Encounter", "Facility", "Organization",
            "Provider", "Care Team", "Programme", "Geography Node",
        ]]],
    },
    {"dt": "Programme"},
    "Form DocType",
    {"dt": "Number Card", "filters": [["name", "like", "UHIS - %"]]},
    {"dt": "SVADatatable Configuration", "filters": [["name", "in", [
        "Patient", "Household", "Case", "Encounter", "Facility", "Organization",
        "Provider", "Care Team", "Programme", "Geography Node",
    ]]]},
    {"dt": "Workspace",   "filters": [["module", "=", "Uhis Next Core"]]},
]

# Periodic jobs — guarded internally by UHIS Settings.fhir_sync_enabled flag.
scheduler_events = {
    "hourly": [
        "uhis_next_core.fhir.pull.do_pull",
    ],
}

# doc_events wiring.
# Rules:
#   guardrail 1 — on_submit of program-form DocTypes → extract_observations
#   guardrail 2 — validate on any DocType (*) → validate_clinical_fields
#   invariant 4 — on_update of Observation/Encounter → reject_mutation (THEN advance_sync_seq)
#   change journal — after_insert + on_update of all transactional DocTypes → advance_sync_seq
#   FHIR push  — after_insert (all 5 mapped types) + on_update (Patient/Case/Condition)
#
# NOTE: Python dicts cannot have duplicate top-level keys.
doc_events = {
    # guardrail 2: reject any clinical DocField missing its sva_ft concept mapping
    "*": {
        "validate": "uhis_next_core.hooks_impl.validate_clinical_fields",
    },
    # invariant 4 + change journal + FHIR push for Observation (append-only fact)
    "Observation": {
        "after_insert": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
            "uhis_next_core.risk.score.compute_risk",
            "uhis_next_core.ai.case_narrative._mark_summary_stale",
        ],
        "on_update": [
            "uhis_next_core.overrides.append_only_guard.reject_mutation",
            "uhis_next_core.hooks_impl.advance_sync_seq",
        ],
    },
    # invariant 4 + change journal + FHIR push for Encounter (append-only fact)
    "Encounter": {
        "after_insert": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
            "uhis_next_core.risk.score.update_last_encounter_dt",
        ],
        "on_update": [
            "uhis_next_core.overrides.append_only_guard.reject_mutation",
            "uhis_next_core.hooks_impl.advance_sync_seq",
        ],
    },
    # change journal + FHIR push for Patient
    "Patient": {
        "after_insert": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
        "on_update": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
    },
    # change journal + FHIR push for Case (EpisodeOfCare)
    "Case": {
        "after_insert": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
        "on_update": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
    },
    # change journal + FHIR push for Condition
    "Condition": {
        "after_insert": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
        "on_update": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
    },
    # change journal only (no FHIR resource in scope)
    "Household": {
        "after_insert": "uhis_next_core.hooks_impl.advance_sync_seq",
        "on_update": "uhis_next_core.hooks_impl.advance_sync_seq",
    },
    "Referral": {
        "after_insert": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
        "on_update": [
            "uhis_next_core.hooks_impl.advance_sync_seq",
            "uhis_next_core.fhir.push.enqueue_fhir_push",
        ],
    },
    "Task": {
        "after_insert": "uhis_next_core.hooks_impl.advance_sync_seq",
        "on_update": "uhis_next_core.hooks_impl.advance_sync_seq",
    },
}
