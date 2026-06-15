// Copyright (c) 2026, Medtronic Labs and contributors
// For license information, please see license.txt

// ── Shared utilities ──────────────────────────────────────────────────────────

function _fmt_date(s) {
	if (!s) return "—";
	const parts = String(s).split("-");
	if (parts.length < 3) return s;
	const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
	return `${months[parseInt(parts[1], 10) - 1]} ${parseInt(parts[2], 10)}, ${parts[0]}`;
}

function _age(dob_str) {
	if (!dob_str) return "—";
	const d = new Date(dob_str), t = new Date();
	let a = t.getFullYear() - d.getFullYear();
	if (t < new Date(t.getFullYear(), d.getMonth(), d.getDate())) a--;
	return a + " yrs";
}

// ── List view settings ────────────────────────────────────────────────────────

frappe.listview_settings["Case"] = {
	get_indicator(doc) {
		const status_map = {
			"Active":   ["Active",   "green",  "status,=,Active"],
			"Referred": ["Referred", "orange", "status,=,Referred"],
			"Resolved": ["Resolved", "blue",   "status,=,Resolved"],
			"Closed":   ["Closed",   "gray",   "status,=,Closed"],
		};
		const risk_map = {
			"High":     ["High Risk",  "red",    "risk_level,=,High"],
			"Moderate": ["Moderate",   "orange", "risk_level,=,Moderate"],
		};
		const status_ind = status_map[doc.status] || ["—", "gray"];
		const risk_ind   = risk_map[doc.risk_level];
		return risk_ind || status_ind;
	},
	add_fields: ["risk_level"],
};

// ── Form controller ───────────────────────────────────────────────────────────

const _CASE_EDIT_FIELDS = [
	"client_uuid", "patient", "programme", "care_team", "status",
	"opened_on", "closed_on", "outcome", "risk_level",
];

frappe.ui.form.on("Case", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Hide raw inputs — summary card shows read-only view
		_CASE_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 1));

		frm.add_custom_button("✏ Edit Details", function() {
			_CASE_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 0));
			frm.scroll_to_field("patient");
			frm.set_intro("Edit fields below. Save to return to view mode.", "blue");
		});

		const $wrapper = frm.get_field("sva_case_summary").$wrapper;
		$wrapper.html(
			`<p style="color:#64748B;padding:8px 0;font-size:13px">Loading case summary…</p>`
		);

		frappe.call({
			method: "uhis_next_core.api.case_summary.get_case_summary",
			args:   { case: frm.doc.name },
			callback(r) {
				if (r.message) _render_case_summary(frm, r.message);
			},
		});
	},
});

// ── Render functions ──────────────────────────────────────────────────────────

function _risk_badge(level) {
	if (!level) return "";
	const cls = { "Low": "uhis-risk-low", "Moderate": "uhis-risk-moderate", "High": "uhis-risk-high" };
	const icon = { "Low": "✓", "Moderate": "!", "High": "■" };
	return `<span class="uhis-risk-badge ${cls[level] || ''}">${icon[level] || "·"} ${level} Risk</span>`;
}

function _cond_chip(c) {
	const cls = c.status === "Active" ? "active" : c.status === "Resolved" ? "resolved" : "inactive";
	return `<span class="uhis-cond-chip ${cls}">● ${c.label} (${c.status})</span>`;
}

function _render_case_summary(frm, d) {
	// ── Header ────────────────────────────────────────────────────────────────
	const days_label = d.days_open != null ? `Opened ${d.days_open} day${d.days_open !== 1 ? "s" : ""} ago` : "";
	const meta_parts = [d.patient_gender, _age(d.patient_dob)].filter(Boolean);
	if (d.care_team) meta_parts.push(d.care_team);

	const prog_chip = d.programme
		? `<span class="uhis-prog-chip active">● ${d.programme}</span>` : "";
	const status_colors = {
		"Active":   "uhis-prog-chip active",
		"Referred": "uhis-prog-chip referred",
		"Closed":   "uhis-prog-chip closed",
		"Resolved": "uhis-prog-chip closed",
	};
	const status_chip = d.status
		? `<span class="${status_colors[d.status] || 'uhis-prog-chip closed'}">${d.status}</span>` : "";

	const header = `
		<div class="uhis-profile-header">
			<div class="uhis-header-body">
				<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
					${prog_chip}${status_chip}
					<span style="color:rgba(255,255,255,0.7);font-size:12px">${days_label}</span>
				</div>
				<div class="uhis-header-name" style="margin-top:6px">${d.patient_name}</div>
				${meta_parts.length ? `<div class="uhis-header-meta">${meta_parts.join(" · ")}</div>` : ""}
			</div>
		</div>`;

	// ── Risk row ──────────────────────────────────────────────────────────────
	let risk_html = "";
	if (d.risk_level) {
		risk_html = `
			<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap">
				<span class="uhis-overview-label">RISK</span>
				${_risk_badge(d.risk_level)}
				${d.risk_factors ? `<span style="font-size:12px;color:#64748B">${d.risk_factors}</span>` : ""}
			</div>`;
	}

	// ── Last visit + care gap ─────────────────────────────────────────────────
	let visit_html = "";
	if (d.last_encounter) {
		const e     = d.last_encounter;
		const parts = [e.encounter_type, e.facility].filter(Boolean);
		const provider_str = e.provider ? ` · ${e.provider}` : "";
		visit_html = `
			<div class="uhis-last-visit-strip" style="margin-bottom:8px">
				<div>
					<span>Last visit: </span>
					<strong style="color:#1E293B">${_fmt_date(e.encounter_dt)}</strong>
					${parts.length ? `<span style="color:#CBD5E1;margin:0 5px">·</span>${parts.join(" · ")}${provider_str}` : ""}
				</div>
				<span class="uhis-visit-count-badge">${d.encounter_count} visit${d.encounter_count !== 1 ? "s" : ""}</span>
			</div>`;
	}

	let gap_html = "";
	if (d.days_since_encounter != null) {
		if (d.days_since_encounter > 60) {
			gap_html = `<div class="uhis-care-gap-alert critical">⚠ No encounter in ${d.days_since_encounter} days</div>`;
		} else if (d.days_since_encounter > 30) {
			gap_html = `<div class="uhis-care-gap-alert warn">⚠ No encounter in ${d.days_since_encounter} days</div>`;
		}
	}

	// ── Vitals ────────────────────────────────────────────────────────────────
	const vitals = d.latest_vitals || {};
	const vital_keys = Object.keys(vitals);
	let vitals_html = "";
	if (vital_keys.length) {
		const cards = vital_keys.map(k => {
			const v       = vitals[k];
			const cls     = v.is_alert ? "uhis-alert" : "uhis-ok";
			const has_thr = v.is_alert !== undefined;
			return `<div class="uhis-vital-card ${has_thr ? cls : ''}">
				<div class="uhis-vital-label">${v.label}</div>
				<div class="uhis-vital-value ${has_thr ? cls : ''}">${v.value}${v.unit ? `<span class="uhis-vital-unit">${v.unit}</span>` : ""}</div>
				${has_thr ? `<div class="uhis-vital-status ${cls}">${v.is_alert ? "⚠ High" : "✓ Normal"}</div>` : ""}
				<div class="uhis-vital-date">${_fmt_date(v.date)}</div>
			</div>`;
		}).join("");
		vitals_html = `
			<div class="uhis-section-label" style="margin-top:12px">Latest Vitals</div>
			<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px">${cards}</div>`;
	}

	// ── Conditions ────────────────────────────────────────────────────────────
	let cond_html = "";
	if (d.conditions && d.conditions.length) {
		const chips = d.conditions.map(_cond_chip).join(" ");
		cond_html = `
			<div class="uhis-overview-row" style="margin-bottom:8px">
				<span class="uhis-overview-label">CONDITIONS</span>
				<div style="display:flex;flex-wrap:wrap;gap:5px">${chips}</div>
			</div>`;
	}

	// ── Tasks ─────────────────────────────────────────────────────────────────
	let tasks_html = "";
	if (d.open_task_count > 0) {
		const overdue_str = d.overdue_tasks && d.overdue_tasks.length
			? ` <span class="uhis-task-overdue">⚠ ${d.overdue_tasks.length} overdue: "${d.overdue_tasks[0].description}"</span>`
			: "";
		tasks_html = `
			<div class="uhis-overview-row" style="margin-bottom:8px">
				<span class="uhis-overview-label">TASKS</span>
				<span>${d.open_task_count} open${overdue_str}</span>
			</div>`;
	}

	// ── Referral ──────────────────────────────────────────────────────────────
	let ref_html = "";
	if (d.referral) {
		const r = d.referral;
		const ref_cls = r.status === "Pending" ? "referred" : "active";
		ref_html = `
			<div class="uhis-overview-row">
				<span class="uhis-overview-label">REFERRAL</span>
				<span>→ ${r.to_facility || "—"} <span class="uhis-prog-chip ${ref_cls}" style="font-size:10px;padding:2px 8px">${r.status}</span></span>
			</div>`;
	}

	// ── AI panel ──────────────────────────────────────────────────────────────
	const ai_html = _render_ai_panel(frm, d);

	frm.get_field("sva_case_summary").$wrapper.html(
		`<div style="padding:4px 0 8px">
			${header}
			<div style="padding:4px 0">
				${risk_html}
				${visit_html}
				${gap_html}
				${vitals_html}
				${cond_html}
				${tasks_html}
				${ref_html}
				${ai_html}
			</div>
		</div>`
	);

	// Typewriter reveal + chat wiring (shared ai_panel.js)
	if (window.uhis_init_case_ai) uhis_init_case_ai(frm, d);

	// Auto-trigger AI generation if stale
	if (d.ai_insights_enabled && (!d.ai_summary || !d.ai_summary_generated_at)) {
		_trigger_ai_generation(frm);
	}
}

function _render_ai_panel(frm, d) {
	if (!d.ai_insights_enabled) {
		return `<div class="uhis-ai-panel">
			<div class="uhis-ai-panel-header">
				<span class="uhis-ai-label">🤖 AI Summary</span>
			</div>
			<span class="uhis-ai-configure-note">AI insights available — configure API key in <a href="/app/uhis-settings" target="_blank">UHIS Settings</a></span>
		</div>`;
	}

	if (!d.ai_summary || !d.ai_summary_generated_at) {
		return `<div class="uhis-ai-panel" id="uhis-ai-panel-${frm.doc.name}">
			<div class="uhis-ai-panel-header">
				<span class="uhis-ai-label">🤖 AI Summary</span>
			</div>
			<span class="uhis-ai-placeholder" id="uhis-ai-status-${frm.doc.name}">Generating summary…</span>
		</div>`;
	}

	const text_lines = d.ai_summary.split("\n\n— Generated ");
	const meta_text  = text_lines[1] ? `Generated ${text_lines[1]}` : "";

	return `<div class="uhis-ai-panel">
		<div class="uhis-ai-panel-header">
			<span class="uhis-ai-label">🤖 AI Summary</span>
			<button class="uhis-btn-ai" id="uhis-regen-btn-${frm.doc.name}" onclick="uhis_regenerate_ai('${frm.doc.name}')">↺ Regenerate</button>
		</div>
		<div class="uhis-ai-text" id="uhis-ai-text-${frm.doc.name}"></div>
		${meta_text ? `<div class="uhis-ai-meta">${frappe.utils.escape_html(meta_text)}</div>` : ""}
		<div id="uhis-chat-section-${frm.doc.name}"
			style="margin-top:14px;border-top:1px solid rgba(226,232,240,0.8);padding-top:10px;">
			<div id="uhis-chat-msgs-${frm.doc.name}"
				style="max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:2px;padding:0 0 8px;scroll-behavior:smooth;"></div>
			<div id="uhis-chat-input-row-${frm.doc.name}"
				style="display:flex;align-items:center;margin-top:10px;background:#F8FAFC;border:1.5px solid #E2E8F0;border-radius:14px;padding:4px 4px 4px 14px;">
				<input id="uhis-chat-input-${frm.doc.name}" type="text" placeholder="Ask a follow-up question…" autocomplete="off"
					style="flex:1;border:none;background:transparent;outline:none;font-size:13px;color:#1E293B;padding:5px 0;min-width:0;font-family:inherit;" />
				<button id="uhis-chat-send-btn-${frm.doc.name}" title="Send"
					style="flex-shrink:0;width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:10px;background:#0E7490;color:#fff;border:none;cursor:pointer;font-size:16px;line-height:1;">&#x2191;</button>
			</div>
		</div>
	</div>`;
}

function _trigger_ai_generation(frm) {
	frappe.call({
		method: "uhis_next_core.ai.case_narrative.enqueue_narrative",
		args:   { case_name: frm.doc.name },
		callback() {
			// Poll once after 5s to see if the job finished
			setTimeout(() => {
				frappe.db.get_value("Case", frm.doc.name,
					["ai_summary", "ai_summary_generated_at"],
					(vals) => {
						if (vals && vals.ai_summary) {
							frm.reload_doc();
						}
					}
				);
			}, 5000);
		},
	});
}

// Exposed globally so the inline onclick handler can reach it
window.uhis_regenerate_ai = function(case_name) {
	const btn = document.querySelector(`.uhis-btn-ai`);
	if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }
	frappe.db.set_value("Case", case_name, "ai_summary_generated_at", null).then(() => {
		frappe.call({
			method: "uhis_next_core.ai.case_narrative.enqueue_narrative",
			args:   { case_name },
			callback() {
				setTimeout(() => cur_frm && cur_frm.reload_doc(), 6000);
			},
		});
	});
};
