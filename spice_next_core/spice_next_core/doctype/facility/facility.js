// Copyright (c) 2026, Medtronic Labs and contributors
// For license information, please see license.txt

const _FACILITY_EDIT_FIELDS = ["label", "facility_type", "organization", "geography_node"];

frappe.ui.form.on("Facility", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Hide raw inputs — AI summary card is the overview
		_FACILITY_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 1));

		frm.add_custom_button("✏ Edit Details", function() {
			_FACILITY_EDIT_FIELDS.forEach(fn => frm.set_df_property(fn, "hidden", 0));
			frm.scroll_to_field("label");
			frm.set_intro("Edit fields below. Save to return to view mode.", "blue");
		});

		frm.add_custom_button("Generate Facility Summary", () => {
			_generate_facility_ai(frm);
		}, "AI");

		_render_facility_ai_panel(frm);
	},
});

function _render_facility_ai_panel(frm) {
	frappe.db.get_value(
		"Spice Settings", "Spice Settings", "ai_insights_enabled",
		(cfg) => {
			const d = {
				ai_insights_enabled:    !!(cfg && cfg.ai_insights_enabled),
				ai_summary:             frm.doc.ai_summary,
				ai_summary_generated_at: frm.doc.ai_summary_generated_at,
			};

			if (!d.ai_insights_enabled || (!d.ai_summary && !d.ai_summary_generated_at)) {
				const field = frm.get_field("sva_ai_summary");
				if (!field) return;
				const note = d.ai_insights_enabled
					? `Click <strong>AI → Generate Facility Summary</strong> to create a situation report for this facility.`
					: `AI insights available — configure API key in <a href="/app/spice-settings" target="_blank">Spice Settings</a>`;
				field.$wrapper.html(`<div class="uhis-ai-panel">
					<div class="uhis-ai-panel-header"><span class="uhis-ai-label">🤖 Facility AI Summary</span></div>
					<span class="uhis-ai-configure-note">${note}</span>
				</div>`);
				return;
			}

			if (window.uhis_render_ai_panel) {
				uhis_render_ai_panel(frm, "sva_ai_summary", d, { label: "Facility AI Summary", doctype: "Facility" });
				const regen = document.getElementById("uhis-regen-btn-" + frm.doc.name);
				if (regen) regen.onclick = function() { uhis_regenerate_facility_ai(frm.doc.name); };
			}
		}
	);
}

function _generate_facility_ai(frm) {
	frappe.show_alert({ message: "Generating facility summary…", indicator: "blue" });
	const field = frm.get_field("sva_ai_summary");
	if (field) {
		field.$wrapper.html(`<div class="uhis-ai-panel">
			<div class="uhis-ai-panel-header"><span class="uhis-ai-label">🤖 Facility AI Summary</span></div>
			<span class="uhis-ai-placeholder">Generating summary…</span>
		</div>`);
	}
	frappe.call({
		method: "spice_next_core.ai.facility_narrative.enqueue_facility_narrative",
		args:   { facility_name: frm.doc.name },
		callback() {
			setTimeout(() => {
				frappe.db.get_value("Facility", frm.doc.name,
					["ai_summary", "ai_summary_generated_at"],
					(vals) => { if (vals && vals.ai_summary) frm.reload_doc(); }
				);
			}, 15000);
		},
	});
}

window.uhis_regenerate_facility_ai = function(facility_name) {
	const btn = document.querySelector(".uhis-btn-ai");
	if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }
	frappe.db.set_value("Facility", facility_name, "ai_summary_generated_at", null).then(() => {
		_generate_facility_ai(cur_frm);
	});
};
