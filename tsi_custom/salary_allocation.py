# apps/tsi_custom/tsi_custom/salary_allocation.py
"""Per-component salary allocation on Salary Structure Assignment.

Selecting a Salary Structure on an assignment fills a grid with every component
that structure defines. Each row carries the amount allocated to that employee
for that component.

How payroll reads it
--------------------
A Salary Detail row only picks up a value if it is formula-based and its formula
names a variable in the evaluation namespace. That namespace is built by
``hrms/payroll/doctype/salary_slip/salary_slip.py::get_data_for_eval``, which
does ``data.update(self._salary_structure_assignment)`` -- and that attribute is
a **flat row**, fetched with ``frappe.db.get_value(..., "*")`` at
``salary_slip.py:803``. Child rows are therefore invisible to formulas.

So each allocation row publishes a variable named ``alloc_<ABBR>``, injected by
:class:`~tsi_custom.overrides.salary_slip.TSISalarySlip`, which extends
``get_data_for_eval`` and nothing else. :func:`wire_salary_structure_formulas`
points each Salary Structure row at its own variable.

Effective dating
----------------
Changing an amount does **not** rewrite the submitted assignment. It creates the
next one, dated from the effective date, carrying everything else forward. hrms
picks the assignment for a slip with ``from_date <= <slip start> ORDER BY
from_date DESC``, so a slip reads the amounts in force for its own period and a
regenerated old slip reproduces its original figures.
"""

import re

import frappe
from frappe import _
from frappe.utils import add_days, cstr, flt, getdate

#: Table field added to Salary Structure Assignment by this app's fixtures.
ALLOCATION_FIELD = "custom_tsi_allocations"

#: Read-only field holding the sum of the Earning rows. The contracted monthly
#: allocation, NOT the paid gross -- a slip prorates and rounds each component
#: independently, so the two legitimately differ.
TOTAL_FIELD = "custom_tsi_total_salary"

#: Salary Detail flags marking a row this feature must not touch. An income-tax
#: row (``variable_based_on_taxable_salary``) is computed by hrms from the tax
#: slab -- giving it a formula stops it being treated as tax at all. A flexible
#: benefit is driven by its own claim/max-benefit logic, and a statistical
#: component is an intermediate that other formulas read.
UNMANAGED_FLAGS = ("variable_based_on_taxable_salary", "is_flexible_benefit", "statistical_component")

#: Prefix for the formula variable each row publishes. Keeps these names clear
#: of a component's own abbreviation, which core already seeds into the same
#: namespace via get_component_abbr_map().
VARIABLE_PREFIX = "alloc_"


# -- Formula variables --------------------------------------------------------


def formula_variable(abbr: str, salary_component: str | None = None) -> str:
	"""Build the eval-namespace name a component's amount is published under.

	An abbreviation is free text, so it is reduced to a Python identifier before
	it can be used in a formula.
	"""
	token = re.sub(r"\W+", "_", cstr(abbr) or cstr(salary_component)).strip("_")
	if not token:
		token = "unnamed"
	if token[0].isdigit():
		token = f"c{token}"
	return f"{VARIABLE_PREFIX}{token}"


# -- Reading the structure ----------------------------------------------------


@frappe.whitelist()
def get_structure_components(salary_structure: str) -> list[dict]:
	"""Every component a Salary Structure defines, earnings first.

	Drives the grid on the assignment form.
	"""
	frappe.has_permission("Salary Structure", "read", throw=True)
	return _structure_components(salary_structure)


def _structure_components(salary_structure: str) -> list[dict]:
	"""Same, without the permission check.

	Kept separate so ``validate`` on an assignment does not fail for a user (or a
	background job) that may create assignments without holding read on Salary
	Structure -- the link is already on the document by then.
	"""
	rows = frappe.get_all(
		"Salary Detail",
		filters={"parent": salary_structure, "parenttype": "Salary Structure"},
		fields=["salary_component", "abbr", "parentfield", "idx", *UNMANAGED_FLAGS],
		order_by="parentfield desc, idx asc",
	)

	components, seen = [], {}
	for row in rows:
		if _is_unmanaged(row):
			continue

		component_type = "Earning" if row.parentfield == "earnings" else "Deduction"
		if row.salary_component in seen:
			if seen[row.salary_component] != component_type:
				# One component, one allocated amount, one variable -- the model
				# cannot say "1,000 as an earning and 300 as a deduction".
				frappe.throw(
					_(
						"{0} is listed as both an earning and a deduction in Salary Structure {1}. "
						"Allocation cannot give it two different amounts -- use separate components."
					).format(frappe.bold(row.salary_component), frappe.bold(salary_structure)),
					title=_("Component on Both Sides"),
				)
			continue

		seen[row.salary_component] = component_type
		abbr = _resolve_abbr(row)
		components.append(
			{
				"salary_component": row.salary_component,
				"abbr": abbr,
				"component_type": component_type,
				"formula_variable": formula_variable(abbr, row.salary_component),
			}
		)
	return components


def _is_unmanaged(row) -> bool:
	"""True for a row hrms computes itself -- see :data:`UNMANAGED_FLAGS`."""
	return any(row.get(flag) for flag in UNMANAGED_FLAGS)


def _resolve_abbr(row) -> str:
	"""A Salary Detail row's abbr, falling back to the component master.

	Shared by the grid and the wiring helper so the two can never disagree on
	the variable name -- a row with a blank abbr would otherwise be wired to a
	variable nobody publishes.
	"""
	return row.abbr or frappe.get_cached_value(
		"Salary Component", row.salary_component, "salary_component_abbr"
	)


# -- Document events ----------------------------------------------------------


def sync_allocations(doc, method=None) -> None:
	"""Keep the allocation grid in step with the selected Salary Structure.

	Additive on purpose: rows the structure defines are added if missing and
	their derived fields refreshed, but a row the structure no longer has is
	left in place rather than silently dropped with its amount. Extras are
	reported instead, so the operator decides.

	Registered on Salary Structure Assignment ``validate``.
	"""
	if not doc.meta.has_field(ALLOCATION_FIELD):
		return

	# validate runs before core checks mandatory fields, so a half-filled grid
	# row can still be present here. Drop it and let core report it.
	rows = [row for row in doc.get(ALLOCATION_FIELD) or [] if row.salary_component]
	if len(rows) != len(doc.get(ALLOCATION_FIELD) or []):
		doc.set(ALLOCATION_FIELD, rows)

	existing = {row.salary_component: row for row in rows}
	_reject_duplicate_components(existing, rows)

	defined = _structure_components(doc.salary_structure) if doc.salary_structure else []
	defined_names = {component["salary_component"] for component in defined}

	for component in defined:
		row = existing.get(component["salary_component"])
		if not row:
			row = doc.append(ALLOCATION_FIELD, {"salary_component": component["salary_component"]})
		row.abbr = component["abbr"]
		row.component_type = component["component_type"]
		row.formula_variable = component["formula_variable"]

	_reject_variable_collisions(doc.get(ALLOCATION_FIELD) or [])

	extras = [
		row.salary_component
		for row in doc.get(ALLOCATION_FIELD) or []
		if row.salary_component not in defined_names
	]
	if extras:
		frappe.msgprint(
			_(
				"These rows are not part of {0} and will not reach payroll: {1}. "
				"Remove them, or add the components to the structure."
			).format(frappe.bold(doc.salary_structure), frappe.bold(", ".join(cstr(c) for c in extras))),
			title=_("Components Not in the Structure"),
			indicator="orange",
		)

	_order_allocations(doc)


def _reject_duplicate_components(existing: dict, rows: list) -> None:
	if len(existing) != len(rows):
		seen, duplicates = set(), set()
		for row in rows:
			if row.salary_component in seen:
				duplicates.add(row.salary_component)
			seen.add(row.salary_component)
		frappe.throw(
			_("{0} appears more than once in the allocation. Each component may be listed only once.").format(
				frappe.bold(", ".join(sorted(cstr(c) for c in duplicates)))
			),
			title=_("Duplicate Component"),
		)


def _reject_variable_collisions(rows: list) -> None:
	"""Two components whose abbreviations reduce to the same identifier would
	overwrite each other in the formula namespace, silently."""
	by_variable = {}
	for row in rows:
		# An extra row the structure does not define has no variable. Those are
		# reported by sync_allocations, not rejected -- do not let two of them
		# collide on the empty string and hard-block the save.
		if not row.formula_variable:
			continue
		by_variable.setdefault(row.formula_variable, []).append(row.salary_component)

	clashes = {variable: names for variable, names in by_variable.items() if len(names) > 1}
	if clashes:
		detail = "; ".join(f"{variable}: {', '.join(names)}" for variable, names in clashes.items())
		frappe.throw(
			_(
				"These components produce the same formula variable, so their amounts would "
				"overwrite each other: {0}. Give them distinct abbreviations."
			).format(frappe.bold(detail)),
			title=_("Clashing Component Abbreviations"),
		)


def _order_allocations(doc) -> None:
	"""Earnings first, then deductions, each alphabetical -- so the grid reads
	the same on every assignment."""
	rows = sorted(
		doc.get(ALLOCATION_FIELD) or [],
		key=lambda row: (row.component_type != "Earning", cstr(row.salary_component)),
	)
	for index, row in enumerate(rows, start=1):
		row.idx = index
	doc.set(ALLOCATION_FIELD, rows)


def set_total_salary(doc, method=None) -> None:
	"""Sum the Earning rows into the read-only total.

	Registered on Salary Structure Assignment ``validate``, after
	:func:`sync_allocations`.
	"""
	if not doc.meta.has_field(TOTAL_FIELD):
		return

	doc.set(
		TOTAL_FIELD,
		sum(flt(row.amount) for row in doc.get(ALLOCATION_FIELD) or [] if row.component_type == "Earning"),
	)


# -- Reading an assignment's allocations --------------------------------------


def get_allocations(assignment: str) -> list[dict]:
	"""Allocation rows for one assignment. Used by the Salary Slip override."""
	return frappe.get_all(
		"TSI Salary Allocation",
		filters={"parent": assignment, "parenttype": "Salary Structure Assignment"},
		fields=["salary_component", "abbr", "component_type", "formula_variable", "amount"],
		order_by="idx asc",
	)


@frappe.whitelist()
def get_component_history(employee: str, salary_component: str) -> list[dict]:
	"""One component's allocated amount across the employee's assignment chain.

	Newest first. ``effective_until`` comes from the next submitted assignment;
	the newest submitted row is open-ended and flagged ``is_current``.
	"""
	# get_list (not get_all) so record-level permissions apply to salary data.
	assignments = frappe.get_list(
		"Salary Structure Assignment",
		filters={"employee": employee, "docstatus": ["<", 2]},
		fields=["name", "from_date", "docstatus", "salary_structure", "owner", "modified"],
		order_by="from_date desc, creation desc",
		limit_page_length=0,
	)
	if not assignments:
		return []

	amounts = {
		row.parent: flt(row.amount)
		for row in frappe.get_all(
			"TSI Salary Allocation",
			filters={
				"parent": ["in", [a["name"] for a in assignments]],
				"parenttype": "Salary Structure Assignment",
				"salary_component": salary_component,
			},
			fields=["parent", "amount"],
		)
	}

	# Only submitted assignments are visible to payroll, so only they close a
	# window. A draft between two submitted rows must not appear to end the
	# earlier one's period.
	submitted_dates = [a["from_date"] for a in assignments if a["docstatus"] == 1]

	today = getdate()

	current_seen = False
	for assignment in assignments:
		assignment["value"] = amounts.get(assignment["name"], 0.0)

		if assignment["docstatus"] == 1:
			# A future-dated assignment is real but not yet in force -- badging it
			# Current would point at the wrong figure for this month's payroll.
			assignment["is_scheduled"] = getdate(assignment["from_date"]) > today
			assignment["is_current"] = not assignment["is_scheduled"] and not current_seen
			if assignment["is_current"]:
				current_seen = True
			successor = next(
				(date for date in reversed(submitted_dates) if date > assignment["from_date"]),
				None,
			)
			assignment["effective_until"] = add_days(successor, -1) if successor else None
		else:
			assignment["is_scheduled"] = False
			assignment["is_current"] = False
			assignment["effective_until"] = None

	return assignments


# -- Changing allocations -----------------------------------------------------


@frappe.whitelist()
def change_allocations(
	employee: str,
	effective_from: str,
	changes,
	notes: str | None = None,
	source_assignment: str | None = None,
) -> dict:
	"""Create the next Salary Structure Assignment with new component amounts.

	:param changes: ``{salary_component: amount}``. May arrive as a JSON string.
	:param source_assignment: the assignment the caller was looking at. Checked
		against the one actually in force, so the caller cannot diff against a
		superseded row without noticing.
	"""
	frappe.has_permission("Salary Structure Assignment", "create", throw=True)

	if isinstance(changes, str):
		changes = frappe.parse_json(changes)
	if not isinstance(changes, dict) or not changes:
		frappe.throw(_("No component changes were supplied."))

	effective_from = getdate(effective_from)
	requested = {cstr(component): flt(amount) for component, amount in changes.items()}

	source_name = _assignment_on(employee, effective_from)
	if not source_name:
		frappe.throw(
			_(
				"{0} has no submitted Salary Structure Assignment on or before {1}. "
				"Create the first assignment manually, then change allocations from there."
			).format(
				frappe.bold(employee),
				frappe.bold(frappe.format(effective_from, {"fieldtype": "Date"})),
			),
			title=_("No Assignment to Carry Forward"),
		)

	if source_assignment and source_assignment != source_name:
		frappe.throw(
			_(
				"You are viewing assignment {0}, but {1} is the one in force on {2}. "
				"Open {1} and change the allocation there."
			).format(
				frappe.bold(source_assignment),
				frappe.bold(source_name),
				frappe.bold(frappe.format(effective_from, {"fieldtype": "Date"})),
			),
			title=_("Not the Assignment in Force"),
		)

	_guard_chain(employee, effective_from)

	previous = frappe.get_doc("Salary Structure Assignment", source_name)
	current = {row.salary_component: flt(row.amount) for row in previous.get(ALLOCATION_FIELD) or []}

	unknown = sorted(set(requested) - set(current))
	if unknown:
		if not current:
			frappe.throw(
				_(
					"Assignment {0} has no component allocation yet -- it predates this feature. "
					"Open it, save it once so the grid fills from {1}, then change the amounts."
				).format(frappe.bold(source_name), frappe.bold(previous.salary_structure)),
				title=_("Allocation Not Set Up"),
			)
		frappe.throw(
			_("{0} is not allocated on assignment {1}. Add it to Salary Structure {2} first.").format(
				frappe.bold(", ".join(unknown)),
				frappe.bold(source_name),
				frappe.bold(previous.salary_structure),
			),
			title=_("Unknown Component"),
		)

	diff = {
		component: {"before": current[component], "after": amount}
		for component, amount in requested.items()
		if current[component] != amount
	}
	if not diff:
		frappe.throw(
			_("The supplied amounts already match assignment {0}. Nothing to change.").format(
				frappe.bold(source_name)
			)
		)

	# copy_doc carries every field and child row forward -- the allocation grid,
	# income_tax_slab, currency, payroll cost centres, and payroll_payable_account,
	# without which a Payroll Entry silently drops the employee from the run.
	#
	# ignore_no_copy=False clears no_copy fields. On this DocType that is
	# amended_from alone, which must not follow a copy into a fresh document.
	assignment = frappe.copy_doc(previous, ignore_no_copy=False)

	# copy_doc only clears docstatus when local.flags.in_test is false, so under
	# the test harness the copy would be inserted straight at docstatus 1 and
	# skip the draft-then-submit path entirely. Set it explicitly.
	assignment.docstatus = 0

	# Mid-year tax openings belong to the assignment that opened the payroll
	# period. get_opening_for() only ignores them while
	# from_date < payroll_period.start_date, so carrying them onto an assignment
	# dated inside the current period would re-arm a stale opening on every
	# remaining slip -- and warn_about_missing_opening_entries stays silent,
	# because it only fires when the fields are empty.
	for fieldname in ("taxable_earnings_till_date", "tax_deducted_till_date"):
		if assignment.meta.has_field(fieldname):
			assignment.set(fieldname, 0)

	assignment.from_date = effective_from
	for row in assignment.get(ALLOCATION_FIELD) or []:
		if row.salary_component in requested:
			row.amount = requested[row.salary_component]

	assignment.insert()
	assignment.submit()

	assignment.add_comment("Comment", _build_change_comment(previous.name, diff, notes))

	return {
		"name": assignment.name,
		"from_date": cstr(effective_from),
		"previous_assignment": previous.name,
		"changes": diff,
		"already_paid": _warn_about_paid_periods(employee, effective_from),
	}


def _assignment_on(employee: str, on_date) -> str | None:
	"""Name of the submitted assignment in force on ``on_date``, if any."""
	return frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee, "docstatus": 1, "from_date": ["<=", on_date]},
		"name",
		order_by="from_date desc",
	)


def _guard_chain(employee: str, effective_from) -> None:
	"""Refuse changes that would be meaningless or ambiguous.

	Either an assignment already starts on that exact date (core raises a bare
	DuplicateAssignment here -- this says what to do instead), or a later
	assignment already exists, in which case inserting behind it would leave the
	newer one still in force and the change would appear to do nothing.
	"""
	clash = frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee, "docstatus": 1, "from_date": effective_from},
		"name",
	)
	if clash:
		frappe.throw(
			_(
				"Assignment {0} already starts on {1}. Pick a different effective date, "
				"or cancel and amend {0} to correct it."
			).format(
				frappe.bold(clash),
				frappe.bold(frappe.format(effective_from, {"fieldtype": "Date"})),
			),
			title=_("Effective Date Already Used"),
		)

	later = frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee, "docstatus": 1, "from_date": [">", effective_from]},
		["name", "from_date"],
		order_by="from_date asc",
		as_dict=True,
	)
	if later:
		frappe.throw(
			_(
				"Assignment {0} already takes effect on {1}, after the date you entered. "
				"Back-dating behind it would have no effect on payroll. "
				"Cancel the later assignment first, or use a date after {1}."
			).format(
				frappe.bold(later.name),
				frappe.bold(frappe.format(later.from_date, {"fieldtype": "Date"})),
			),
			title=_("A Later Assignment Exists"),
		)


def _warn_about_paid_periods(employee: str, effective_from) -> dict | None:
	"""Flag a back-dated change over periods that are already paid.

	Deliberately not a hard block: a retro increment ("effective 1 March,
	approved in September") is routine, and core permits it. The submitted slips
	keep their figures, so nothing is corrupted -- but the arrears they now owe
	will not appear by themselves, and the operator has to know that.
	"""
	paid = frappe.get_all(
		"Salary Slip",
		filters={"employee": employee, "docstatus": 1, "start_date": [">=", effective_from]},
		fields=["name", "start_date"],
		order_by="start_date asc",
	)
	if not paid:
		return None

	frappe.msgprint(
		_(
			"{0} salary slip(s) are already submitted for periods on or after {1}, "
			"starting with {2}. They keep the amounts they were paid at -- this change "
			"does not create arrears. Settle any difference separately, "
			"for example with an Additional Salary."
		).format(
			frappe.bold(len(paid)),
			frappe.bold(frappe.format(effective_from, {"fieldtype": "Date"})),
			frappe.bold(paid[0].name),
		),
		title=_("Periods Already Paid"),
		indicator="orange",
	)
	return {"count": len(paid), "first_slip": paid[0].name, "first_period": cstr(paid[0].start_date)}


def _build_change_comment(previous_name: str, diff: dict, notes: str | None) -> str:
	lines = [_("Carried forward from {0}.").format(previous_name)]
	for component, change in diff.items():
		lines.append(
			"{}: {} -> {}".format(
				component,
				frappe.format(change["before"], {"fieldtype": "Currency"}),
				frappe.format(change["after"], {"fieldtype": "Currency"}),
			)
		)
	if notes:
		lines.append(_("Notes: {0}").format(notes))
	return "<br>".join(lines)


# -- Adoption on an existing site ---------------------------------------------


def _assignments_missing_allocations(salary_structure: str) -> list[str]:
	"""Submitted assignments for this structure that carry no allocation rows.

	``validate`` never runs on a submitted document, so an assignment created
	before this app was installed cannot grow its grid by being re-saved. It has
	to be backfilled.
	"""
	assignments = frappe.get_all(
		"Salary Structure Assignment",
		filters={"salary_structure": salary_structure, "docstatus": 1},
		pluck="name",
	)
	if not assignments:
		return []

	allocated = set(
		frappe.get_all(
			"TSI Salary Allocation",
			filters={"parent": ["in", assignments], "parenttype": "Salary Structure Assignment"},
			pluck="parent",
			distinct=True,
		)
	)
	return [name for name in assignments if name not in allocated]


@frappe.whitelist()
def backfill_allocations(salary_structure: str, dry_run: int = 1) -> dict:
	"""Add the allocation grid to assignments that were submitted before this app.

	Rows are created at **amount 0**, deliberately. The real per-employee figures
	are not knowable from the old assignment, and inventing them would pay the
	wrong salary silently. Set the real amounts afterwards with **Change
	Allocation**, which dates a fresh assignment -- then wire the structure.

	Idempotent, and ``dry_run`` by default.
	"""
	frappe.only_for(("System Manager", "HR Manager"))

	dry_run = bool(int(dry_run or 0))
	components = _structure_components(salary_structure)
	if not components:
		frappe.throw(
			_("Salary Structure {0} defines no allocatable components.").format(frappe.bold(salary_structure))
		)

	assignments = frappe.get_all(
		"Salary Structure Assignment",
		filters={"salary_structure": salary_structure, "docstatus": 1},
		fields=["name", "employee"],
		order_by="from_date asc",
	)

	planned = []
	for assignment in assignments:
		present = set(
			frappe.get_all(
				"TSI Salary Allocation",
				filters={"parent": assignment.name, "parenttype": "Salary Structure Assignment"},
				pluck="salary_component",
			)
		)
		missing = [c for c in components if c["salary_component"] not in present]
		if not missing:
			continue

		planned.append(
			{
				"assignment": assignment.name,
				"employee": assignment.employee,
				"adds": [c["salary_component"] for c in missing],
			}
		)

		if dry_run:
			continue

		for offset, component in enumerate(missing, start=len(present) + 1):
			row = frappe.new_doc("TSI Salary Allocation")
			row.update(
				{
					"parent": assignment.name,
					"parenttype": "Salary Structure Assignment",
					"parentfield": ALLOCATION_FIELD,
					"idx": offset,
					"salary_component": component["salary_component"],
					"abbr": component["abbr"],
					"component_type": component["component_type"],
					"formula_variable": component["formula_variable"],
					"amount": 0,
				}
			)
			# Match the parent, which is submitted.
			row.docstatus = 1
			row.insert(ignore_permissions=True)

		frappe.clear_document_cache("Salary Structure Assignment", assignment.name)

	return {
		"salary_structure": salary_structure,
		"dry_run": dry_run,
		"planned": planned,
		"assignments_touched": len(planned),
	}


# -- Salary Structure wiring (admin helper) -----------------------------------


@frappe.whitelist()
def wire_salary_structure_formulas(salary_structure: str, dry_run: int = 1) -> dict:
	"""Point every Salary Structure row at its own allocation variable.

	Without this the amounts are stored on the assignment and never read: a
	Salary Detail row only picks up a value if it is formula-based and its
	formula names the variable.

	Idempotent, and ``dry_run`` by default -- inspect ``planned`` before running
	with ``dry_run=0``.

	Rows whose formula is exactly ``base`` are left alone. That is core's own
	field, still used elsewhere in hrms, and a site that already drives Basic
	from it should keep doing so.

	Income tax, flexible benefit and statistical rows (:data:`UNMANAGED_FLAGS`)
	are left alone too, and reported as ``left_unmanaged``. hrms computes those
	itself -- giving an income-tax row a formula stops it being treated as tax.

	Rows are written with ``frappe.db.set_value`` rather than ``doc.save()``:
	``formula`` and ``condition`` are ``allow_on_submit`` on Salary Detail but
	``amount_based_on_formula`` is not, so saving the parent would throw on a
	submitted structure -- and cancelling the structure to amend it would break
	every assignment already linked to it.
	"""
	frappe.only_for(("System Manager", "HR Manager"))

	dry_run = bool(int(dry_run or 0))

	# Wiring switches the structure over to reading the grid. Any submitted
	# assignment without one would then hit a NameError on its next slip, and a
	# submitted document cannot grow the grid by being re-saved -- validate does
	# not run. Refuse until they are backfilled.
	unallocated = _assignments_missing_allocations(salary_structure)
	if unallocated:
		frappe.throw(
			_(
				"{0} submitted assignment(s) for {1} have no component allocation, "
				"starting with {2}. Payroll would fail for them once the structure is wired. "
				"Run backfill_allocations for this structure first, then set the real amounts "
				"with Change Allocation."
			).format(
				frappe.bold(len(unallocated)),
				frappe.bold(salary_structure),
				frappe.bold(unallocated[0]),
			),
			title=_("Assignments Not Allocated Yet"),
		)

	rows = frappe.get_all(
		"Salary Detail",
		filters={"parent": salary_structure, "parenttype": "Salary Structure"},
		fields=[
			"name",
			"salary_component",
			"abbr",
			"parentfield",
			"amount",
			"amount_based_on_formula",
			"formula",
			*UNMANAGED_FLAGS,
		],
		order_by="parentfield desc, idx asc",
	)
	if not rows:
		frappe.throw(_("Salary Structure {0} has no component rows.").format(frappe.bold(salary_structure)))

	planned, already_wired, left_on_base, left_unmanaged = [], [], [], []
	for row in rows:
		if _is_unmanaged(row):
			# Income tax, flexible benefits and statistical components are hrms'
			# own to compute -- wiring them would silently switch that off.
			left_unmanaged.append(row.salary_component)
			continue

		existing = cstr(row.formula).strip()
		if existing == "base":
			left_on_base.append(row.salary_component)
			continue

		variable = formula_variable(_resolve_abbr(row), row.salary_component)
		if row.amount_based_on_formula and existing == variable:
			already_wired.append(row.salary_component)
			continue

		planned.append(
			{
				"row": row.name,
				"salary_component": row.salary_component,
				"component_type": "Earning" if row.parentfield == "earnings" else "Deduction",
				"formula": variable,
				"previous_formula": existing,
				# A leftover fixed amount alongside a formula row is the classic
				# double-pay trap, so report it whether or not it gets cleared.
				"clears_amount": flt(row.amount),
			}
		)

	if not dry_run:
		for item in planned:
			frappe.db.set_value(
				"Salary Detail",
				item["row"],
				{"amount_based_on_formula": 1, "formula": item["formula"], "amount": 0},
				update_modified=False,
			)
		frappe.clear_document_cache("Salary Structure", salary_structure)

	return {
		"salary_structure": salary_structure,
		"dry_run": dry_run,
		"planned": planned,
		"already_wired": already_wired,
		"left_on_base": left_on_base,
		"left_unmanaged": left_unmanaged,
	}
