// Copyright (c) 2026, Medtronic Labs and contributors
// For license information, please see license.txt

function uhis_initials(name) {
	if (!name) return "?";
	const parts = name.trim().split(/\s+/);
	if (parts.length === 1) return parts[0][0].toUpperCase();
	return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function uhis_age(dob_str) {
	if (!dob_str) return "—";
	const d = new Date(dob_str), t = new Date();
	let a = t.getFullYear() - d.getFullYear();
	if (t < new Date(t.getFullYear(), d.getMonth(), d.getDate())) a--;
	return a + " yrs";
}

function uhis_fmt_date(s) {
	if (!s) return "—";
	const parts = String(s).split("-");
	if (parts.length < 3) return s;
	const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
	return `${months[parseInt(parts[1], 10) - 1]} ${parseInt(parts[2], 10)}, ${parts[0]}`;
}

const _VITALS_META = {
	"LOINC|8480-6":    { label: "Systolic BP",   unit: "mmHg",   warn_above: 139 },
	"LOINC|8462-4":    { label: "Diastolic BP",  unit: "mmHg",   warn_above: 89  },
	"LOINC|29463-7":   { label: "Weight",         unit: "kg"                       },
	"LOINC|2339-0":    { label: "Blood Glucose",  unit: "mmol/L", warn_above: 6.9 },
	"SNOMED|77176002": { label: "Tobacco Use",    unit: ""                         },
	"LOINC|8302-2":    { label: "Height",         unit: "cm"                       },
	"LOINC|39156-5":   { label: "BMI",            unit: "kg/m²",  warn_above: 24.9 },
	"LOINC|4548-4":    { label: "HbA1c",          unit: "%",      warn_above: 6.9  },
	"LOINC|8665-2":    { label: "LMP",            unit: ""                          },
	"LOINC|11996-6":   { label: "Gravida",        unit: ""                          },
	"LOINC|11977-6":   { label: "Parity",         unit: ""                          },
};

const _NCD_CONCEPTS = [
	"LOINC|8480-6", "LOINC|8462-4", "LOINC|2339-0",
	"LOINC|4548-4", "LOINC|29463-7", "LOINC|39156-5",
	"SNOMED|77176002", "LOINC|8302-2",
];
const _MCH_CONCEPTS = ["LOINC|8665-2", "LOINC|11996-6", "LOINC|11977-6"];

const _PATIENT_EDIT_FIELDS = [
	"client_uuid", "full_name", "dob", "gender",
	"phone", "primary_household", "identifiers",
];

frappe.ui.form.on("Patient", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Hide raw demographic inputs — Details tab becomes edit-only on demand
		_PATIENT_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 1));

		frm.add_custom_button("✏ Edit Details", function() {
			_PATIENT_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 0));
			frm.set_active_tab && frm.set_active_tab("tab_details");
			frm.scroll_to_field("full_name");
			frm.set_intro("Edit fields below. Save to return to view mode.", "blue");
		});

		frm.get_field("sva_health_profile").$wrapper.html(
			`<p style="color:#64748B;padding:8px 0;font-size:13px">Loading health summary…</p>`
		);
		frappe.call({
			method: "spice_next_core.api.patient_summary.get_health_summary",
			args: { patient: frm.doc.name },
			callback(r) {
				if (!r.message) return;
				_render_patient_profile(frm, r.message);
				_render_patient_ai_panel(frm, r.message);
				if (r.message.ai_insights_enabled &&
						(!r.message.ai_summary || !r.message.ai_summary_generated_at)) {
					_trigger_patient_ai(frm);
				}
			},
		});
	},
});

function _prog_class(status) {
	if (status === "Active")   return "active";
	if (status === "Referred") return "referred";
	return "closed";
}

function _render_patient_ai_panel(frm, d) {
	if (window.uhis_render_ai_panel) {
		uhis_render_ai_panel(frm, "sva_ai_summary", d, { label: "Patient AI Summary", doctype: "Patient" });
		const regen = document.getElementById("uhis-regen-btn-" + frm.doc.name);
		if (regen) regen.onclick = function() { uhis_regenerate_patient_ai(frm.doc.name); };
	}
}

function _trigger_patient_ai(frm) {
	frappe.call({
		method: "spice_next_core.ai.patient_narrative.enqueue_patient_narrative",
		args:   { patient_name: frm.doc.name },
		callback() {
			setTimeout(() => {
				frappe.db.get_value("Patient", frm.doc.name,
					["ai_summary", "ai_summary_generated_at"],
					(vals) => { if (vals && vals.ai_summary) frm.reload_doc(); }
				);
			}, 8000);
		},
	});
}

window.uhis_regenerate_patient_ai = function(patient_name) {
	const btn = document.querySelector(".uhis-btn-ai");
	if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }
	frappe.db.set_value("Patient", patient_name, "ai_summary_generated_at", null).then(() => {
		frappe.call({
			method: "spice_next_core.ai.patient_narrative.enqueue_patient_narrative",
			args:   { patient_name },
			callback() {
				setTimeout(() => cur_frm && cur_frm.reload_doc(), 8000);
			},
		});
	});
};

function _render_patient_profile(frm, data) {
	const has_mch = (data.cases || []).some(
		c => c.programme === "PW Profile" && c.status === "Active"
	);
	const concepts_to_show = has_mch
		? [..._NCD_CONCEPTS, ..._MCH_CONCEPTS]
		: _NCD_CONCEPTS;

	// ── Header ────────────────────────────────────────────────────────────────
	const initials = uhis_initials(frm.doc.full_name);
	const age      = uhis_age(data.dob);
	const gender   = data.gender || "—";

	const chips = [];
	if (data.phone)
		chips.push(`<span class="uhis-header-chip">📱 ${data.phone}</span>`);
	if (data.primary_household)
		chips.push(`<span class="uhis-header-chip">🏠 ${data.primary_household}</span>`);

	const header = `
		<div class="uhis-profile-header">
			<div class="uhis-avatar" style="width:52px;height:52px;font-size:18px;font-weight:800;border:2px solid rgba(255,255,255,0.4)">
				${initials}
			</div>
			<div class="uhis-header-body">
				<div class="uhis-header-name">${frm.doc.full_name}</div>
				<div class="uhis-header-meta">${gender} · ${age}</div>
				${chips.length ? `<div class="uhis-header-chips">${chips.join("")}</div>` : ""}
			</div>
		</div>`;

	// ── Programme badges ─────────────────────────────────────────────────────
	const cases  = data.cases || [];
	const badges = cases.length
		? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px">${
			cases.map(c => `
				<span class="uhis-prog-chip ${_prog_class(c.status)}">
					● ${c.programme || "—"} · ${c.status || "—"}
				</span>`).join("")
		  }</div>`
		: "";

	// ── Vitals grid ──────────────────────────────────────────────────────────
	const vitals         = data.latest_vitals || {};
	const vital_concepts = concepts_to_show.filter(k => vitals[k]);

	let vitals_html;
	if (!vital_concepts.length) {
		vitals_html = `<div class="uhis-empty-state">No vitals recorded yet</div>`;
	} else {
		const cards = vital_concepts.map(concept => {
			const meta       = _VITALS_META[concept];
			const vdata      = vitals[concept];
			const has_thresh = meta.warn_above !== undefined;
			const is_alert   = has_thresh && parseFloat(vdata.value) > meta.warn_above;
			const is_ok      = has_thresh && !is_alert;
			const state_cls  = !has_thresh ? "" : is_alert ? "uhis-alert" : "uhis-ok";

			return `<div class="uhis-vital-card ${state_cls}">
				<div class="uhis-vital-label">${meta.label}</div>
				<div class="uhis-vital-value ${state_cls}">
					${vdata.value}
					${meta.unit ? `<span class="uhis-vital-unit">${meta.unit}</span>` : ""}
				</div>
				${has_thresh ? `<div class="uhis-vital-status ${state_cls}">${is_alert ? "⚠ High" : "✓ Normal"}</div>` : ""}
				<div class="uhis-vital-date">${uhis_fmt_date(vdata.date)}</div>
			</div>`;
		}).join("");

		vitals_html = `
			<div class="uhis-section-label">Latest Vitals</div>
			<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px">${cards}</div>`;
	}

	// ── Last visit strip ─────────────────────────────────────────────────────
	let visit_html = "";
	if (data.last_encounter) {
		const enc   = data.last_encounter;
		const parts = [enc.encounter_type, enc.facility].filter(Boolean);
		visit_html = `
			<div class="uhis-last-visit-strip">
				<div>
					<span>Last visit: </span>
					<strong style="color:#1E293B">${uhis_fmt_date(enc.encounter_dt)}</strong>
					${parts.length ? `<span style="color:#CBD5E1;margin:0 5px">·</span>${parts.join(" · ")}` : ""}
				</div>
				<span class="uhis-visit-count-badge">
					${data.encounter_count} visit${data.encounter_count !== 1 ? "s" : ""}
				</span>
			</div>`;
	}

	frm.get_field("sva_health_profile").$wrapper.html(
		`<div style="padding:4px 0 8px">${header}${badges}${vitals_html}${visit_html}</div>`
	);
}
