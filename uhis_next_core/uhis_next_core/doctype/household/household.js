// Copyright (c) 2026, Medtronic Labs and contributors
// For license information, please see license.txt

function uhis_initials(name) {
	if (!name) return "?";
	const parts = name.trim().split(/\s+/);
	if (parts.length === 1) return parts[0][0].toUpperCase();
	return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function uhis_age_num(dob_str) {
	if (!dob_str) return null;
	const d = new Date(dob_str), t = new Date();
	let a = t.getFullYear() - d.getFullYear();
	if (t < new Date(t.getFullYear(), d.getMonth(), d.getDate())) a--;
	return a;
}

function uhis_fmt_date(s) {
	if (!s) return "—";
	const parts = String(s).split("-");
	if (parts.length < 3) return s;
	const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
	return `${months[parseInt(parts[1], 10) - 1]} ${parseInt(parts[2], 10)}, ${parts[0]}`;
}

const _HOUSEHOLD_EDIT_FIELDS = [
	"section_break_qbdq", "client_uuid", "address",
	"geography_node", "socioeconomic_tier", "members",
];

frappe.ui.form.on("Household", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Hide raw household fields — overview tab shows members/cases/encounters + AI
		_HOUSEHOLD_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 1));

		frm.add_custom_button("✏ Edit Details", function() {
			_HOUSEHOLD_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 0));
			frm.scroll_to_field("address");
			frm.set_intro("Edit fields below. Save to return to view mode.", "blue");
		});

		["members_html", "cases_html", "encounters_html"].forEach(fn => {
			const field = frm.get_field(fn);
			if (field) {
				field.$wrapper.html(
					`<p style="color:#64748B;font-size:12px">Loading…</p>`
				);
			}
		});
		frappe.call({
			method: "uhis_next_core.api.household_summary.get_household_summary",
			args: { household: frm.doc.name },
			callback(r) {
				if (!r.message) return;
				_render_household(frm, r.message);
				_render_household_ai_panel(frm, r.message);
				if (r.message.ai_insights_enabled &&
						(!r.message.ai_summary || !r.message.ai_summary_generated_at)) {
					_trigger_household_ai(frm);
				}
			},
		});
	},
});

function _render_household_ai_panel(frm, d) {
	if (window.uhis_render_ai_panel) {
		uhis_render_ai_panel(frm, "sva_ai_summary", d, { label: "Household AI Summary", doctype: "Household" });
		const regen = document.getElementById("uhis-regen-btn-" + frm.doc.name);
		if (regen) regen.onclick = function() { uhis_regenerate_household_ai(frm.doc.name); };
	}
}

function _trigger_household_ai(frm) {
	frappe.call({
		method: "uhis_next_core.ai.household_narrative.enqueue_household_narrative",
		args:   { household_name: frm.doc.name },
		callback() {
			setTimeout(() => {
				frappe.db.get_value("Household", frm.doc.name,
					["ai_summary", "ai_summary_generated_at"],
					(vals) => { if (vals && vals.ai_summary) frm.reload_doc(); }
				);
			}, 8000);
		},
	});
}

window.uhis_regenerate_household_ai = function(household_name) {
	const btn = document.querySelector(".uhis-btn-ai");
	if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }
	frappe.db.set_value("Household", household_name, "ai_summary_generated_at", null).then(() => {
		frappe.call({
			method: "uhis_next_core.ai.household_narrative.enqueue_household_narrative",
			args:   { household_name },
			callback() {
				setTimeout(() => cur_frm && cur_frm.reload_doc(), 8000);
			},
		});
	});
};

function _member_row(m) {
	const age = uhis_age_num(m.dob);
	const sex = m.gender ? m.gender[0] : null;
	const meta_parts = [age !== null ? `${age}y` : null, sex].filter(Boolean);

	const active_progs = (m.programmes || []).filter(p => p.status === "Active").map(p => p.programme);
	const prog_text = active_progs.length
		? `<span style="font-size:10px;color:#0E7490;font-weight:600">${active_progs.join(", ")}</span>`
		: `<span style="font-size:10px;color:#CBD5E1">—</span>`;

	const bp_val  = m.bp_sys  ? parseFloat(m.bp_sys)  : null;
	const glc_val = m.glucose ? parseFloat(m.glucose) : null;

	const flags = [];
	if (bp_val !== null) {
		const cls = bp_val > 139 ? "alert" : "ok";
		flags.push(`<span class="uhis-flag ${cls}">BP ${bp_val}</span>`);
	}
	if (glc_val !== null) {
		const cls = glc_val > 6.9 ? "alert" : "ok";
		flags.push(`<span class="uhis-flag ${cls}">Glc ${glc_val}</span>`);
	}

	const sep = meta_parts.length ? `<span style="color:#CBD5E1;margin:0 4px">·</span>` : "";

	return `<div class="uhis-member-row">
		<div class="uhis-avatar sm">${uhis_initials(m.full_name)}</div>
		<div class="uhis-member-info">
			<a class="uhis-member-name" href="/app/patient/${encodeURIComponent(m.name)}">${m.full_name}</a>
			<div class="uhis-member-meta">${meta_parts.join(" · ")}${sep}${prog_text}</div>
		</div>
		<div style="display:flex;gap:4px;flex-shrink:0">${flags.join("")}</div>
	</div>`;
}

function _render_household(frm, data) {
	const members        = data.members || [];
	const active_by_prog = data.active_by_prog || {};
	const total_enc      = data.total_encounters || 0;
	const last_dt        = data.last_encounter_dt;

	// ── Members ──────────────────────────────────────────────────────────────
	const members_html = members.length
		? `<div class="uhis-section-label">Members · ${members.length}</div>
		   <div>${members.map(_member_row).join("")}</div>`
		: `<div class="uhis-empty-state">No members linked</div>`;

	// ── Cases ────────────────────────────────────────────────────────────────
	const prog_entries = Object.entries(active_by_prog);
	let cases_html;
	if (!prog_entries.length) {
		cases_html = `<div class="uhis-empty-state">No active enrolments</div>`;
	} else {
		const total = prog_entries.reduce((s, [, n]) => s + n, 0);
		const rows = prog_entries.map(([prog, count]) =>
			`<div class="uhis-enrolment-row">
				<span class="uhis-enrolment-name">${prog}</span>
				<span class="uhis-enrolment-count-badge">${count}</span>
			</div>`
		).join("");
		cases_html = `<div class="uhis-section-label">Active Enrolments · ${total}</div><div>${rows}</div>`;
	}

	// ── Encounters ───────────────────────────────────────────────────────────
	const no_visits = `<div class="uhis-empty-state" style="margin-bottom:0">No visits yet</div>`;
	const encounters_html = `
		<div class="uhis-section-label">Visit History</div>
		<div class="uhis-visit-counter">
			<div class="uhis-visit-number">${total_enc}</div>
			<div class="uhis-visit-label">Total Visits</div>
		</div>
		${last_dt
			? `<div class="uhis-last-visit-card">
				<div class="uhis-lvc-label">Last Visit</div>
				<div class="uhis-lvc-value">${uhis_fmt_date(last_dt)}</div>
			</div>`
			: no_visits
		}`;

	frm.get_field("members_html").$wrapper.html(members_html);
	frm.get_field("cases_html").$wrapper.html(cases_html);
	frm.get_field("encounters_html").$wrapper.html(encounters_html);
}
