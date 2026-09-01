// apps/tsi_custom/tsi_custom/public/js/salary_structure_assignment.js
//
// Salary component allocation on Salary Structure Assignment.
//
// A clock button (history) and a pencil button (change) are injected beside each
// allocatable component field. The pencil does NOT edit this assignment -- it
// creates the next one, dated from the effective date, so payroll for earlier
// periods keeps reproducing its original figures.

frappe.provide("tsi_custom.salary_allocation");

const API = "tsi_custom.salary_allocation";
const BTN_CLASS = "tsi-alloc-btns";

frappe.ui.form.on("Salary Structure Assignment", {
	refresh(frm) {
		if (frm.is_new()) return;
		load_allocation_fields().then((fields) => inject_buttons(frm, fields));
	},

	after_save(frm) {
		load_allocation_fields().then((fields) => inject_buttons(frm, fields));
	},
});

// The server owns the component list, so a site whose Custom Fields have not
// been migrated yet renders whatever exists instead of erroring.
function load_allocation_fields() {
	if (tsi_custom.salary_allocation._fields) {
		return Promise.resolve(tsi_custom.salary_allocation._fields);
	}
	return frappe
		.xcall(`${API}.get_allocation_fields`)
		.then((fields) => {
			tsi_custom.salary_allocation._fields = fields || [];
			return tsi_custom.salary_allocation._fields;
		})
		.catch(() => []);
}

function inject_buttons(frm, fields) {
	(fields || []).forEach((field) => inject_for_field(frm, field));
}

function inject_for_field(frm, field) {
	const control = frm.fields_dict[field.fieldname];
	if (!control || !control.$wrapper) return;
	if (control.$wrapper.find(`.${BTN_CLASS}`).length) return; // already injected

	const $label = control.$wrapper.find(".control-label").first();
	if (!$label.length) return;

	const $btns = $(`
		<span class="${BTN_CLASS}" style="float:right;display:inline-flex;gap:3px;margin-top:1px;">
			<button class="btn btn-xs btn-default tsi-alloc-history"
				title="${frappe.utils.escape_html(__("History of {0}", [field.label]))}"
				style="padding:2px 6px;line-height:1.2;">${icon_clock()}</button>
			<button class="btn btn-xs btn-primary tsi-alloc-change"
				title="${frappe.utils.escape_html(__("Change {0} from a date", [field.label]))}"
				style="padding:2px 6px;line-height:1.2;">${icon_pencil()}</button>
		</span>
	`);

	$label.append($btns);
	$btns.find(".tsi-alloc-history").on("click", (e) => {
		e.stopPropagation();
		show_history_dialog(frm, field);
	});
	$btns.find(".tsi-alloc-change").on("click", (e) => {
		e.stopPropagation();
		show_change_dialog(frm, field);
	});
}

// -- Change dialog ------------------------------------------------------------

function show_change_dialog(frm, field) {
	const current = flt(frm.doc[field.fieldname]);

	const dialog = new frappe.ui.Dialog({
		title: __("Change {0}", [field.label]),
		fields: [
			{
				fieldname: "component",
				fieldtype: "Data",
				label: __("Component"),
				default: field.label,
				read_only: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "effective_from",
				fieldtype: "Date",
				label: __("Effective From"),
				reqd: 1,
				default: frappe.datetime.month_start(frappe.datetime.add_months(frappe.datetime.get_today(), 1)),
				description: __(
					"A new Salary Structure Assignment is created from this date. Payroll for periods starting before it is unaffected."
				),
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "current_value",
				fieldtype: "Currency",
				label: __("Current Value"),
				default: current,
				read_only: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "new_value",
				fieldtype: "Currency",
				label: __("New Value"),
				reqd: 1,
				default: current,
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes (optional)") },
		],
		primary_action_label: __("Create Assignment"),
		primary_action(values) {
			dialog.disable_primary_action();

			frappe
				.xcall(`${API}.change_component_values`, {
					employee: frm.doc.employee,
					effective_from: values.effective_from,
					changes: { [field.fieldname]: values.new_value },
					notes: values.notes || null,
					// So the server can refuse if this is not the assignment
					// actually in force -- the Current Value shown below came
					// from this record.
					source_assignment: frm.doc.name,
				})
				.then((result) => {
					dialog.hide();
					frappe.show_alert(
						{
							message: __("{0} effective {1} on assignment {2}", [
								field.label,
								frappe.format(values.effective_from, { fieldtype: "Date" }),
								result.name,
							]),
							indicator: "green",
						},
						7
					);
					frappe.set_route("Form", "Salary Structure Assignment", result.name);
				})
				.catch(() => dialog.enable_primary_action());
		},
	});

	dialog.show();
}

// -- History dialog -----------------------------------------------------------

function show_history_dialog(frm, field) {
	const dialog = new frappe.ui.Dialog({
		title: __("History — {0}", [field.label]),
		size: "large",
	});

	const $body = $('<div class="tsi-alloc-history-body"></div>').appendTo(dialog.$body);
	$body.html(`<div class="text-muted">${__("Loading…")}</div>`);
	dialog.show();

	frappe
		.xcall(`${API}.get_component_history`, {
			employee: frm.doc.employee,
			fieldname: field.fieldname,
		})
		.then((rows) => render_history($body, rows || [], frm.doc.name))
		.catch(() => $body.html(`<div class="text-danger">${__("Could not load history.")}</div>`));
}

function render_history($body, rows, current_name) {
	if (!rows.length) {
		$body.html(`<div class="text-muted text-center" style="padding:20px;">${__("No assignments found.")}</div>`);
		return;
	}

	const header = [
		__("Effective From"),
		__("Effective Until"),
		__("Amount"),
		__("Change"),
		__("Assignment"),
		__("Status"),
	];

	// Only submitted assignments are visible to payroll, so a delta is only
	// meaningful between two of them -- a draft in between must not be treated
	// as the previous value.
	const submitted = rows.filter((row) => row.docstatus === 1);

	const body_rows = rows.map((row) => {
		let delta = null;
		if (row.docstatus === 1) {
			// rows are newest-first, so the next submitted entry is the previous one.
			const position = submitted.indexOf(row);
			const previous = submitted[position + 1];
			if (previous) delta = flt(row.value) - flt(previous.value);
		}

		return `
			<tr>
				<td>${frappe.format(row.from_date, { fieldtype: "Date" })}</td>
				<td>${
					row.effective_until
						? frappe.format(row.effective_until, { fieldtype: "Date" })
						: '<span class="text-muted">—</span>'
				}</td>
				<td style="text-align:right;font-family:monospace;font-weight:600;">
					${frappe.format(row.value, { fieldtype: "Currency" })}
				</td>
				<td style="text-align:right;font-family:monospace;">${format_delta(delta)}</td>
				<td>${assignment_link(row.name, current_name)}</td>
				<td>${status_badge(row)}</td>
			</tr>`;
	});

	$body.html(`
		<div style="overflow-x:auto;">
			<table class="table table-bordered table-sm" style="font-size:12px;margin:0;">
				<thead><tr>${header.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
				<tbody>${body_rows.join("")}</tbody>
			</table>
		</div>
		<div class="text-muted" style="font-size:11px;margin-top:8px;">
			${__("A Salary Slip uses the newest submitted assignment whose Effective From is on or before the slip's start date.")}
		</div>
	`);
}

function format_delta(delta) {
	if (delta === null || !delta) return '<span class="text-muted">—</span>';
	const formatted = frappe.format(Math.abs(delta), { fieldtype: "Currency" });
	return delta > 0
		? `<span class="text-success">+${formatted}</span>`
		: `<span class="text-danger">−${formatted}</span>`;
}

function assignment_link(name, current_name) {
	const label = frappe.utils.escape_html(name);
	const href = `/app/salary-structure-assignment/${encodeURIComponent(name)}`;
	const marker = name === current_name ? ` <span class="text-muted">(${__("this one")})</span>` : "";
	return `<a href="${href}">${label}</a>${marker}`;
}

function status_badge(row) {
	if (row.docstatus === 0) {
		return `<span class="indicator-pill red">${__("Draft")}</span>`;
	}
	return row.is_current
		? `<span class="indicator-pill green">${__("Current")}</span>`
		: `<span class="indicator-pill gray">${__("Superseded")}</span>`;
}

// -- Icons --------------------------------------------------------------------

function icon_clock() {
	return `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
		stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
		<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;
}

function icon_pencil() {
	return `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
		stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
		<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
		<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
}
