"""Seed the Sync Seq Counter single row on first migration."""
import frappe


def execute():
    if not frappe.db.get_single_value("Sync Seq Counter", "current_seq"):
        frappe.db.set_single_value("Sync Seq Counter", "current_seq", 0)
