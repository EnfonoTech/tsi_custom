// apps/tsi_custom/tsi_custom/public/js/salary_structure_assignment.js
//
// Component allocation on Salary Structure Assignment.
//
// Selecting a Salary Structure fills the allocation grid with that structure's
// components. On a submitted assignment, "Change Allocation" does not edit this
// record -- it creates the next one, dated from the effective date, so payroll
// for earlier periods keeps reproducing its original figures.

const API = "tsi_custom.salary_allocation";
const TABLE = "custom_tsi_allocations";

frappe.ui.form.on("Salary Structure Assignment", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 1) return;

		frm.add_custom_button(__("Change Allocation"), () => show_change_dialog(frm));
		frm.add_custom_button(__("Allocation History"), () => show_history_dialog(frm));
	},

	salary_structure(frm) {
		if (!frm.doc.salary_structure) {
			frm.clear_table(TABLE);
			frm.refresh_field(TABLE);
			return;
		}
		fill_from_structure(frm);
	},
});

// -- Fill the grid from the structure -----------------------------------------

function fill_from_structure(frm) {
	frappe
		.xcall(`${API}.get_structure_components`, { salary_structure: frm.doc.salary_structure })
		.then((components) => {
			if (!components) return;

			// Keep amounts already entered for components the new structure also has.
			const kept = {};
			(frm.doc[TABLE] || []).forEach((row) => {
				kept[row.salary_component] = row.amount;
			});

			frm.clear_table(TABLE);
			components.forEach((component) => {
				const row = frm.add_child(TABLE, component);
				row.amount = kept[component.salary_component] || 0;
			});
			frm.refresh_field(TABLE);

			const dropped = Object.keys(kept).filter(
				(name) => !components.some((c) => c.salary_component === name)
			);
			if (dropped.length) {
				frappe.show_alert(
					{
						message: __("Removed components not in this structure: {0}", [dropped.join(", ")]),
						indicator: "orange",
					},
					7
				);
			}
		});
}

// -- Change allocation --------------------------------------------------------

function show_change_dialog(frm) {
	const rows = frm.doc[TABLE] || [];
	if (!rows.length) {
		frappe.msgprint(__("This assignment has no allocated components."));
		return;
	}

	// The Table control reads and writes this array in place, so it has to be a
	// stable reference held outside the dialog -- returning dialog.get_value()
	// from get_data would be circular. Same shape erpnext uses for its own
	// "Update Items" dialog (erpnext/public/js/utils.js).
	const allocations = rows.map((row) => ({
		salary_component: row.salary_component,
		component_type: row.component_type,
		current_amount: flt(row.amount),
		new_amount: flt(row.amount),
	}));

	const dialog = new frappe.ui.Dialog({
		title: __("Change Allocation"),
		size: "extra-large",
		fields: [
			{
				fieldname: "effective_from",
				fieldtype: "Date",
				label: __("Effective From"),
				reqd: 1,
				// month_start() takes no argument -- it always means the current
				// month -- so shift it with add_months, which does read its date.
				default: frappe.datetime.add_months(frappe.datetime.month_start(), 1),
				description: __(
					"A new Salary Structure Assignment is created from this date. Payroll for periods starting before it is unaffected."
				),
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "allocations",
				fieldtype: "Table",
				label: __("Components"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				in_place_edit: false,
				data: allocations,
				get_data: () => allocations,
				// grid.js starts total_colsize at 1 and drops any column that
				// pushes it past 11, so these must sum to 10 or less -- at 11 the
				// editable "New" column disappears without a word.
				fields: [
					{
						fieldname: "salary_component",
						fieldtype: "Data",
						label: __("Component"),
						in_list_view: 1,
						read_only: 1,
						columns: 3,
					},
					{
						fieldname: "component_type",
						fieldtype: "Data",
						label: __("Type"),
						in_list_view: 1,
						read_only: 1,
						columns: 2,
					},
					{
						fieldname: "current_amount",
						fieldtype: "Currency",
						label: __("Current"),
						in_list_view: 1,
						read_only: 1,
						columns: 2,
					},
					{
						fieldname: "new_amount",
						fieldtype: "Currency",
						label: __("New"),
						in_list_view: 1,
						columns: 3,
					},
				],
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes (optional)") },
		],
		primary_action_label: __("Create Assignment"),
		primary_action(values) {
			const changes = {};
			allocations.forEach((row) => {
				if (flt(row.new_amount) !== flt(row.current_amount)) {
					changes[row.salary_component] = flt(row.new_amount);
				}
			});

			if (!Object.keys(changes).length) {
				frappe.msgprint(__("No amounts were changed."));
				return;
			}

			dialog.disable_primary_action();
			frappe
				.xcall(`${API}.change_allocations`, {
					employee: frm.doc.employee,
					effective_from: values.effective_from,
					changes: changes,
					notes: values.notes || null,
					// So the server can refuse if this is not the assignment
					// actually in force -- the Current amounts came from here.
					source_assignment: frm.doc.name,
				})
				.then((result) => {
					dialog.hide();
					frappe.show_alert(
						{
							message: __("Assignment {0} created, effective {1}", [
								result.name,
								frappe.format(values.effective_from, { fieldtype: "Date" }),
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

// -- History ------------------------------------------------------------------

function show_history_dialog(frm) {
	const rows = frm.doc[TABLE] || [];
	if (!rows.length) {
		frappe.msgprint(__("This assignment has no allocated components."));
		return;
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Allocation History"),
		size: "large",
		fields: [
			{
				fieldname: "salary_component",
				fieldtype: "Select",
				label: __("Component"),
				options: rows.map((row) => row.salary_component).join("\n"),
				default: rows[0].salary_component,
				onchange: () => load(dialog.get_value("salary_component")),
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "history", fieldtype: "HTML" },
		],
	});

	function load(component) {
		const $area = dialog.get_field("history").$wrapper;
		$area.html(`<div class="text-muted">${__("Loading…")}</div>`);

		frappe
			.xcall(`${API}.get_component_history`, {
				employee: frm.doc.employee,
				salary_component: component,
			})
			.then((history) => render_history($area, history || [], frm.doc.name))
			.catch(() => $area.html(`<div class="text-danger">${__("Could not load history.")}</div>`));
	}

	dialog.show();
	load(rows[0].salary_component);
}

function render_history($area, rows, current_name) {
	if (!rows.length) {
		$area.html(`<div class="text-muted text-center" style="padding:20px;">${__("No assignments found.")}</div>`);
		return;
	}

	// Only submitted assignments are visible to payroll, so a delta is only
	// meaningful between two of them.
	const submitted = rows.filter((row) => row.docstatus === 1);

	const header = [
		__("Effective From"),
		__("Effective Until"),
		__("Amount"),
		__("Change"),
		__("Assignment"),
		__("Status"),
	];

	const body = rows.map((row) => {
		let delta = null;
		if (row.docstatus === 1) {
			const previous = submitted[submitted.indexOf(row) + 1];
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

	$area.html(`
		<div style="overflow-x:auto;">
			<table class="table table-bordered table-sm" style="font-size:12px;margin:0;">
				<thead><tr>${header.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
				<tbody>${body.join("")}</tbody>
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
	const href = `/app/salary-structure-assignment/${encodeURIComponent(name)}`;
	const marker = name === current_name ? ` <span class="text-muted">(${__("this one")})</span>` : "";
	return `<a href="${href}">${frappe.utils.escape_html(name)}</a>${marker}`;
}

function status_badge(row) {
	if (row.docstatus === 0) return `<span class="indicator-pill red">${__("Draft")}</span>`;
	// Submitted but not yet in force -- payroll is still using an earlier one.
	if (row.is_scheduled) return `<span class="indicator-pill blue">${__("Scheduled")}</span>`;
	return row.is_current
		? `<span class="indicator-pill green">${__("Current")}</span>`
		: `<span class="indicator-pill gray">${__("Superseded")}</span>`;
}
