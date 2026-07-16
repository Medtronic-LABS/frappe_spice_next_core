"""Backfill the AI widget toggle + VAD tuning defaults onto the existing
UHIS Settings single row.

These fields were added with a JSON `default`, but Frappe only applies a
DocField default when a *new* document is created — `bench migrate`
creates the new columns on the pre-existing Single row with MariaDB's own
column default (0 / 0.0), not the DocField's intended default. Without
this patch, `controls.get_controls` would report every AI widget as off
and every VAD parameter as 0 for every site that already had a UHIS
Settings row before this change, silently reversing the intended "all AI
widgets on, factory VAD tuning" default.

Runs unconditionally rather than guarding on "unset" — a stored 0 is
indistinguishable from "never configured" once the column exists, and
this patch only ever runs once per site (Frappe's Patch Log), immediately
after the columns are created and before any admin has had a chance to
open the desk UI and set a real value, so there's nothing to accidentally
clobber.
"""

import frappe

_AI_WIDGET_DEFAULTS = {
	"step1_summary_enabled": 1,
	"step1_asr_enabled": 1,
	"step2_asr_enabled": 1,
	"step3_summary_enabled": 1,
	"step3_referral_alert_enabled": 1,
	"step3_whatsapp_enabled": 1,
}

_VAD_TUNING_DEFAULTS = {
	"vad_enter_margin_db": 9,
	"vad_sustain_margin_db": 6,
	"vad_floor_ceiling_dbfs": -35,
	"vad_floor_alpha": 0.08,
	"vad_bootstrap_ms": 500,
	"vad_debounce_ms": 180,
	"vad_hangover_ms": 700,
	"vad_preroll_ms": 350,
}


def execute():
	for fieldname, default in {**_AI_WIDGET_DEFAULTS, **_VAD_TUNING_DEFAULTS}.items():
		frappe.db.set_single_value("UHIS Settings", fieldname, default)
