app_name = "spice_next_core"
app_title = "Spice Next Core"
app_publisher = "Medtronic Labs"
app_description = "Multi-country public health platform — server of record and sync API provider."
app_email = "admin@medtroniclabs.org"
app_license = "GPL-3.0-or-later"
app_version = "0.1.0"

# frappe_theme provides the SVADatatable/dashboard/report chart config layer this app
# builds on — required so `bench install-app spice_next_core` installs it automatically.
required_apps = ["frappe_theme"]

# Post-login landing page for System Users (Administrator, SK/SS desk logins)
# -- frappe.website.utils.get_home_page() reads this hook directly; without
# it, System User logins fall back to bare "/desk" regardless of
# System Settings.default_app (that setting only governs Website/portal
# users via frappe.apps.get_default_path, a separate mechanism below).
#
# Must be a route that resolves directly via website_route_rules
# ("/desk/<path:app_path>" -> "desk" template) -- NOT the "/app/..." alias.
# get_home_page()'s result is fed straight into path resolution, it is never
# run back through the website_redirects regex that rewrites "/app/(.*)" to
# "/desk/\1" (that regex only fires for a literal incoming request path, e.g.
# a user typing /app in the browser). Using "app/uhis-clinical" here resolved
# to a dead end and 404'd on landing at bare "/" while /app itself worked fine.
home_page = "desk/uhis-clinical"

# Registers this app in the app-switcher screen and makes it a valid target for
# System Settings.default_app / User.default_app (frappe.apps.get_default_path,
# the Website/portal-user landing-page mechanism).
# Route matches the "UHIS Clinical" Workspace.
add_to_apps_screen = [
	{
		"name": app_name,
		"title": app_title,
		"route": "/app/uhis-clinical",
		"has_permission": "frappe.permissions.check_app_permission",
	}
]

# Doctype-level component classes only (.uhis-* — AI panel, vital cards,
# profile headers, chat, risk badges). No generic navbar/sidebar/button
# theme overrides here; the desk otherwise uses default Frappe styling.
app_include_css = ["/assets/spice_next_core/css/uhis_theme.css"]

# Shared AI panel utilities (typewriter, chat, highlight).
app_include_js = ["/assets/spice_next_core/js/ai_panel.js"]

# Fixtures committed to git; bench export-fixtures writes these files.
# Order matters: Number Card must precede Workspace (workspace references card names).
fixtures = [
	"Role",
	"Geography Type",
	"Geography Node",
	"Concept",
	"Clinical Question",
	"Symptom Observation Mapping",
	"Priority Rule",
	"Protocol Selection Rule",
	"Recommendation Rule",
	{
		"dt": "Property Setter",
		"filters": [
			[
				"doc_type",
				"in",
				[
					"Patient",
					"Case",
					"Household",
					"Encounter",
					"Facility",
					"Organization",
					"Provider",
					"Care Team",
					"Programme",
					"Geography Node",
				],
			]
		],
	},
	{"dt": "Programme"},
	"Form DocType",
	{"dt": "Number Card", "filters": [["name", "like", "UHIS - %"]]},
	{
		"dt": "SVADatatable Configuration",
		"filters": [
			[
				"name",
				"in",
				[
					"Patient",
					"Household",
					"Case",
					"Encounter",
					"Facility",
					"Organization",
					"Provider",
					"Care Team",
					"Programme",
					"Geography Node",
				],
			]
		],
	},
]
# Workspace is intentionally NOT a fixture: frappe.model.sync's orphan-cleanup
# deletes any public Workspace whose module+app are set but has no matching
# on-disk <module>/workspace/<name>/<name>.json file, and fixture-delivered
# docs never get that file (v16 upgrade deleted all 3 UHIS workspaces this
# way). Delivered instead as on-disk files under spice_next_core/workspace/.

# Periodic jobs — guarded internally by UHIS Settings.fhir_sync_enabled flag.
scheduler_events = {
	"hourly": [
		"spice_next_core.fhir.pull.do_pull",
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
		"validate": "spice_next_core.hooks_impl.validate_clinical_fields",
	},
	# invariant 4 + change journal + FHIR push for Observation (append-only fact)
	"Observation": {
		"after_insert": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
			"spice_next_core.risk.score.compute_risk",
			"spice_next_core.ai.case_narrative._mark_summary_stale",
		],
		"on_update": [
			"spice_next_core.overrides.append_only_guard.reject_mutation",
			"spice_next_core.hooks_impl.advance_sync_seq",
		],
	},
	# invariant 4 + change journal + FHIR push for Encounter (append-only fact)
	"Encounter": {
		"after_insert": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
			"spice_next_core.risk.score.update_last_encounter_dt",
		],
		"on_update": [
			"spice_next_core.overrides.append_only_guard.reject_mutation",
			"spice_next_core.hooks_impl.advance_sync_seq",
		],
	},
	# change journal + FHIR push for Patient
	"Patient": {
		"after_insert": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
		"on_update": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
	},
	# change journal + FHIR push for Case (EpisodeOfCare)
	# before_insert: denormalize geography_node from Patient → Household
	"Case": {
		"before_insert": "spice_next_core.hooks_impl.set_case_geography_node",
		"after_insert": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
		"on_update": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
	},
	# change journal + FHIR push for Condition
	"Condition": {
		"after_insert": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
		"on_update": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
	},
	# change journal only (no FHIR resource in scope)
	"Household": {
		"after_insert": "spice_next_core.hooks_impl.advance_sync_seq",
		"on_update": "spice_next_core.hooks_impl.advance_sync_seq",
	},
	"Referral": {
		"after_insert": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
		"on_update": [
			"spice_next_core.hooks_impl.advance_sync_seq",
			"spice_next_core.fhir.push.enqueue_fhir_push",
		],
	},
	"Task": {
		"after_insert": "spice_next_core.hooks_impl.advance_sync_seq",
		"on_update": "spice_next_core.hooks_impl.advance_sync_seq",
	},
}
