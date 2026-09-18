"""
One-time fix for the UHIS workspace sidebars.

Frappe v16's persistent left sidebar is driven by a separate 'Workspace Sidebar'
doctype, auto-generated (frappe.utils.install.create_workspace_sidebar_for_workspaces)
the first time a workspace is installed with no matching sidebar fixture already
shipped by the app. That auto-generator only reads Workspace.shortcuts -- it never
looks at Workspace.links (the Card Break groupings) -- so 'spice_next_core'
workspaces (which never shipped a hand-authored workspace_sidebar/*.json the way
frappe core does for Build/Automation/etc.) ended up with a flat, ungrouped
sidebar list, missing any doctype that only exists in links (Household,
Observation) entirely.

This rebuilds each UHIS workspace's Workspace Sidebar Items to mirror the
existing Card Break grouping in Workspace.links, using type='Section Break'
(collapsible) group headers + child=1 items -- the actual mechanism
frappe.ui.sidebar.js's find_nested_items() nests under (NOT the 'Sidebar Item
Group' select option, which despite the name isn't wired to any nesting
behavior in the current frontend).

Deliberately does NOT try to mirror the workspace's New-X shortcuts here:
Workspace Sidebar Item's DocType link resolution (sidebar_item.js get_path())
only ever sets doc_view="List", never "New" -- there is no field on this
doctype the frontend reads to open a create form from the sidebar. A "New
Patient" sidebar entry would silently open the Patient *list*, which is worse
than not having the entry. Quick-create stays on the workspace dashboard's own
shortcut cards (Workspace Shortcut has a real doc_view="New" and is unaffected
by any of this).

Since these Workspace Sidebar docs are standard+app-owned and developer_mode is
on in this bench, saving re-exports the JSON fixture under
spice_next_core/spice_next_core/workspace_sidebar/, so the fix persists across
future bench migrate runs the same way frappe core's own sidebars do.
"""

import frappe

WORKSPACES = ["UHIS Clinical", "UHIS Facility", "UHIS NCD Programme"]


def rebuild(workspace_name):
	ws = frappe.get_doc("Workspace", workspace_name)
	if frappe.db.exists("Workspace Sidebar", workspace_name):
		sidebar = frappe.get_doc("Workspace Sidebar", workspace_name)
		# Reassigning sidebar.items to a fresh list and calling .save() is not
		# reliably idempotent for child tables here -- re-running this script
		# against an already-built sidebar has silently doubled every row
		# (confirmed live: 9/8/5 items became 18/16/10). Delete the existing
		# child rows outright before rebuilding, so a re-run always starts
		# from a clean slate regardless of ORM merge behavior on save().
		frappe.db.delete(
			"Workspace Sidebar Item", {"parent": workspace_name, "parenttype": "Workspace Sidebar"}
		)
		sidebar.reload()
	else:
		sidebar = frappe.new_doc("Workspace Sidebar")
		sidebar.title = workspace_name
		sidebar.header_icon = ws.icon
	# standard+app-owned, matching how frappe core ships Build/Automation/etc.,
	# so this sidebar re-exports to a tracked JSON fixture (see module docstring)
	sidebar.standard = 1
	sidebar.app = "spice_next_core"

	cards = ws.get_link_groups()

	new_items = []
	idx = 0

	home = frappe.new_doc("Workspace Sidebar Item")
	home.update(
		{"type": "Link", "label": "Home", "link_type": "Workspace", "link_to": workspace_name, "idx": idx}
	)
	new_items.append(home)

	for card in cards:
		label = card.get("label")
		card_links = card.get("links") or []
		if not card_links or label == "Link":
			continue

		idx += 1
		section = frappe.new_doc("Workspace Sidebar Item")
		section.update({"type": "Section Break", "label": label, "collapsible": 1, "idx": idx})
		new_items.append(section)

		seen_doctype = set()
		for link in card_links:
			dt = link.get("link_to")
			if dt in seen_doctype:
				continue
			seen_doctype.add(dt)
			if not frappe.db.exists("DocType", dt):
				print(
					f"  [skip] {workspace_name}: {dt} has no DocType (dangling link) -- "
					"pre-existing, unrelated to sidebar fix"
				)
				continue

			idx += 1
			item = frappe.new_doc("Workspace Sidebar Item")
			item.update(
				{
					"type": "Link",
					"child": 1,
					"label": link.get("label") or dt,
					"link_type": "DocType",
					"link_to": dt,
					"idx": idx,
				}
			)
			new_items.append(item)

	sidebar.items = new_items
	sidebar.save()
	print(f"Rebuilt sidebar for {workspace_name}: {len(new_items)} items")


def run():
	for ws in WORKSPACES:
		rebuild(ws)
	frappe.db.commit()
