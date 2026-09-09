// Shared UI helpers — loaded globally via app_include_js.
// Both patient.js and household.js call these without re-declaring them.

function uhis_initials(name) {
	if (!name) return "?";
	const parts = name.trim().split(/\s+/);
	if (parts.length === 1) return parts[0][0].toUpperCase();
	return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Returns "X yrs" string (used on patient detail header).
function uhis_age(dob_str) {
	if (!dob_str) return "—";
	const d = new Date(dob_str), t = new Date();
	let a = t.getFullYear() - d.getFullYear();
	if (t < new Date(t.getFullYear(), d.getMonth(), d.getDate())) a--;
	return a + " yrs";
}

// Returns age as a plain number (used in household member rows).
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
