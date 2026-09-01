# apps/tsi_custom/tsi_custom/salary_allocation.py
"""Effective-dated salary component allocation on Salary Structure Assignment.

Design
------
Each component amount lives in its own Currency field on the Salary Structure
Assignment (SSA). Changing one does **not** rewrite the submitted assignment --
it creates a **new submitted SSA** dated from the effective date, carrying every
other value forward unchanged.

Why this shape, and not an override ledger that rewrites the SSA in place:

* hrms selects the assignment for a Salary Slip with
  ``from_date <= <slip start date> ORDER BY from_date DESC`` and merges every
  field of that row into the formula namespace
  (``hrms/payroll/doctype/salary_slip/salary_slip.py`` ::
  ``set_salary_structure_assignment`` and ``get_data_for_eval``). One assignment
  per effective date therefore gives date-correct payroll for free.
* A slip regenerated for an earlier period still reproduces its original
  figures. Rewriting amounts on a single assignment does the opposite: it
  retroactively repays history.
* Nothing writes to a submitted document, so ``docstatus``, Versions and
  permissions remain the real audit trail -- no scheduler, no
  ``frappe.db.set_value`` into a submitted SSA, no Pending/Applied state machine
  that can silently drift out of sync with what payroll actually reads.

Core only blocks a duplicate assignment on the *same* ``(employee, from_date)``
pair, so a chain of dated assignments is a supported, first-class pattern.

Payroll read path
-----------------
The Salary Structure's earning rows must reference these fieldnames in their
``formula``. :func:`wire_salary_structure_formulas` does that wiring; see the
"Salary allocation" section of the app README for the runbook.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cstr, flt, getdate

#: SSA fields treated as allocatable salary components, in display order.
#: ``base`` is core; the rest ship as Custom Fields in this app's fixtures.
ALLOCATION_FIELDS = (
	"base",
	"custom_tsi_hra_amount",
	"custom_tsi_transport_amount",
	"custom_tsi_other_allowance_amount",
)

#: Read-only field holding the sum of :data:`ALLOCATION_FIELDS`. It is the
#: contracted monthly allocation, NOT the paid gross -- a Salary Slip prorates
#: and rounds each component independently, so the two legitimately differ.
TOTAL_FIELD = "custom_tsi_total_salary"

#: Fieldtypes a component may use. Guards the client-supplied fieldname.
_NUMERIC_FIELDTYPES = ("Currency", "Float", "Int", "Percent")

#: Mid-year income-tax opening balances. They belong to the assignment that
#: opened the employee's payroll period, never to a later one carried forward --
#: hrms' get_opening_for() only ignores them while
#: ``from_date < payroll_period.start_date``, so an assignment dated inside the
#: current period would re-arm a stale opening for every remaining slip.
_TAX_OPENING_FIELDS = ("taxable_earnings_till_date", "tax_deducted_till_date")

#: Default Salary Component -> SSA fieldname wiring for
#: :func:`wire_salary_structure_formulas`. Override per site by passing
#: ``mapping``; these are the labels TSI's structure is expected to use.
DEFAULT_COMPONENT_MAP = {
	"Basic": "base",
	"HRA": "custom_tsi_hra_amount",
	"Transport Allowance": "custom_tsi_transport_amount",
	"Other Allowance": "custom_tsi_other_allowance_amount",
}


# -- Metadata -----------------------------------------------------------------


@frappe.whitelist()
def get_allocation_fields() -> list[dict]:
	"""Return the allocatable component fields that actually exist on the SSA.

	The client renders its buttons from this list, so a site that has not yet
	migrated the Custom Fields degrades to whatever is present instead of
	throwing.
	"""
	frappe.has_permission("Salary Structure Assignment", "read", throw=True)

	meta = frappe.get_meta("Salary Structure Assignment")
	fields = []
	for fieldname in ALLOCATION_FIELDS:
		field = meta.get_field(fieldname)
		if field and field.fieldtype in _NUMERIC_FIELDTYPES:
			fields.append({"fieldname": fieldname, "label": _(field.label or fieldname)})
	return fields


def _validate_allocation_field(fieldname: str):
	"""Resolve a client-supplied fieldname to its DocField, or throw.

	The fieldname reaches the database as a column name, so it is validated
	against the DocType meta rather than trusted.
	"""
	fieldname = cstr(fieldname).strip()
	if fieldname not in ALLOCATION_FIELDS:
		frappe.throw(
			_("{0} is not an allocatable salary component.").format(frappe.bold(fieldname)),
			title=_("Unknown Component"),
		)

	field = frappe.get_meta("Salary Structure Assignment").get_field(fieldname)
	if not field or field.fieldtype not in _NUMERIC_FIELDTYPES:
		frappe.throw(
			_(
				"{0} is missing from Salary Structure Assignment. "
				"Run bench migrate to sync this app's Custom Fields."
			).format(frappe.bold(fieldname)),
			title=_("Component Field Missing"),
		)
	return field


# -- History ------------------------------------------------------------------


@frappe.whitelist()
def get_component_history(employee: str, fieldname: str) -> list[dict]:
	"""Return one component's value across the employee's assignment chain.

	Newest first. ``effective_until`` is derived from the next assignment's
	``from_date``; the newest submitted row is open-ended and flagged
	``is_current``.
	"""
	field = _validate_allocation_field(fieldname)

	# get_list (not get_all) so record-level permissions apply to salary data.
	rows = frappe.get_list(
		"Salary Structure Assignment",
		filters={"employee": employee, "docstatus": ["<", 2]},
		fields=[
			"name",
			"from_date",
			"docstatus",
			"salary_structure",
			"owner",
			"modified",
			field.fieldname,
		],
		order_by="from_date desc, creation desc",
		limit_page_length=0,
	)

	# Only submitted assignments are visible to payroll, so only they close a
	# window. A draft sitting between two submitted rows must not appear to end
	# the earlier one's period.
	submitted_from_dates = [row["from_date"] for row in rows if row["docstatus"] == 1]

	submitted_seen = False
	for row in rows:
		row["value"] = flt(row.pop(field.fieldname, 0))

		if row["docstatus"] == 1:
			row["is_current"] = not submitted_seen
			submitted_seen = True
			successor = next(
				(from_date for from_date in reversed(submitted_from_dates) if from_date > row["from_date"]),
				None,
			)
			row["effective_until"] = add_days(successor, -1) if successor else None
		else:
			row["is_current"] = False
			row["effective_until"] = None

	return rows


# -- Change a component -------------------------------------------------------


@frappe.whitelist()
def change_component_values(
	employee: str,
	effective_from: str,
	changes,
	notes: str | None = None,
	source_assignment: str | None = None,
) -> dict:
	"""Create the next Salary Structure Assignment with new component values.

	:param changes: ``{ssa_fieldname: amount}``. May arrive as a JSON string
		from the client.
	:param source_assignment: the assignment the caller was looking at. Checked
		against the one actually in force so the caller cannot diff against a
		superseded row without noticing.

	Returns the new assignment's name and a per-component before/after diff.
	"""
	frappe.has_permission("Salary Structure Assignment", "create", throw=True)

	if isinstance(changes, str):
		changes = frappe.parse_json(changes)
	if not isinstance(changes, dict) or not changes:
		frappe.throw(_("No component changes were supplied."))

	effective_from = getdate(effective_from)

	requested = {}
	for fieldname, value in changes.items():
		_validate_allocation_field(fieldname)
		requested[fieldname] = flt(value)

	source_name = _assignment_on(employee, effective_from)
	if not source_name:
		frappe.throw(
			_(
				"{0} has no submitted Salary Structure Assignment on or before {1}. "
				"Create the first assignment manually, then change components from there."
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
				"Open {1} and change the component there."
			).format(
				frappe.bold(source_assignment),
				frappe.bold(source_name),
				frappe.bold(frappe.format(effective_from, {"fieldtype": "Date"})),
			),
			title=_("Not the Assignment in Force"),
		)

	_guard_chain(employee, effective_from)

	previous = frappe.get_doc("Salary Structure Assignment", source_name)

	diff = {}
	for fieldname, value in requested.items():
		before = flt(previous.get(fieldname))
		if before != value:
			diff[fieldname] = {"before": before, "after": value}

	if not diff:
		frappe.throw(
			_("The supplied values already match assignment {0}. Nothing to change.").format(
				frappe.bold(source_name)
			)
		)

	# copy_doc carries every field and child row forward (payroll_payable_account,
	# income_tax_slab, currency, payroll cost centres, the untouched components),
	# which matters: a Payroll Entry filters employees on payroll_payable_account,
	# so an assignment created without it is silently dropped from the run.
	#
	# ignore_no_copy=False clears no_copy fields. On this DocType that is
	# amended_from alone, which must not follow a copy into a fresh document.
	assignment = frappe.copy_doc(previous, ignore_no_copy=False)

	# copy_doc only clears docstatus when local.flags.in_test is false, so under
	# the test harness the copy would be inserted straight at docstatus 1 and
	# skip the draft-then-submit path entirely. Set it explicitly.
	assignment.docstatus = 0

	for fieldname in _TAX_OPENING_FIELDS:
		if assignment.meta.has_field(fieldname):
			assignment.set(fieldname, 0)

	assignment.from_date = effective_from
	for fieldname, value in requested.items():
		assignment.set(fieldname, value)

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
	"""Refuse changes that would corrupt the assignment chain.

	Two cases: an assignment already starts on that exact date (core raises a
	bare DuplicateAssignment here -- this says what to do instead), or a later
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
	meta = frappe.get_meta("Salary Structure Assignment")
	lines = [_("Carried forward from {0}.").format(previous_name)]
	for fieldname, change in diff.items():
		field = meta.get_field(fieldname)
		lines.append(
			"{}: {} -> {}".format(
				_(field.label or fieldname) if field else fieldname,
				frappe.format(change["before"], {"fieldtype": "Currency"}),
				frappe.format(change["after"], {"fieldtype": "Currency"}),
			)
		)
	if notes:
		lines.append(_("Notes: {0}").format(notes))
	return "<br>".join(lines)


# -- Document events ----------------------------------------------------------


def set_total_salary(doc, method=None) -> None:
	"""Keep the read-only total in step with the components it sums.

	Registered on Salary Structure Assignment ``validate`` so a manually created
	first assignment is covered too, not only assignments this module builds.
	"""
	if not doc.meta.has_field(TOTAL_FIELD):
		return

	doc.set(
		TOTAL_FIELD,
		sum(flt(doc.get(fieldname)) for fieldname in ALLOCATION_FIELDS if doc.meta.has_field(fieldname)),
	)


# -- Salary Structure wiring (admin helper) -----------------------------------


@frappe.whitelist()
def wire_salary_structure_formulas(salary_structure: str, mapping=None, dry_run: int = 1) -> dict:
	"""Point a Salary Structure's earning rows at the SSA component fields.

	Without this the component fields are written but never read: a Salary
	Detail row only picks up an SSA field if it is formula-based and the formula
	names that field.

	Idempotent, and ``dry_run`` by default -- inspect ``planned`` before running
	with ``dry_run=0``.

	Rows are written with ``frappe.db.set_value`` rather than ``doc.save()``:
	``formula`` and ``condition`` are ``allow_on_submit`` on Salary Detail but
	``amount_based_on_formula`` is not, so saving the parent would throw on a
	submitted structure -- and cancelling the structure to amend it would break
	every assignment already linked to it.
	"""
	frappe.only_for(("System Manager", "HR Manager"))

	dry_run = bool(int(dry_run or 0))
	if isinstance(mapping, str):
		mapping = frappe.parse_json(mapping)
	mapping = mapping or DEFAULT_COMPONENT_MAP

	for fieldname in set(mapping.values()):
		_validate_allocation_field(fieldname)

	rows = frappe.get_all(
		"Salary Detail",
		filters={
			"parent": salary_structure,
			"parenttype": "Salary Structure",
			"parentfield": "earnings",
		},
		fields=["name", "salary_component", "amount", "amount_based_on_formula", "formula"],
		order_by="idx asc",
	)
	if not rows:
		frappe.throw(_("Salary Structure {0} has no earning rows.").format(frappe.bold(salary_structure)))

	planned, already_wired, unmapped = [], [], []
	for row in rows:
		fieldname = mapping.get(row.salary_component)
		if not fieldname:
			unmapped.append(row.salary_component)
			continue

		if row.amount_based_on_formula and cstr(row.formula).strip() == fieldname:
			already_wired.append(row.salary_component)
			continue

		planned.append(
			{
				"row": row.name,
				"salary_component": row.salary_component,
				"formula": fieldname,
				"previous_formula": cstr(row.formula).strip(),
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
				{
					"amount_based_on_formula": 1,
					"formula": item["formula"],
					"amount": 0,
				},
				update_modified=False,
			)
		frappe.clear_document_cache("Salary Structure", salary_structure)

	return {
		"salary_structure": salary_structure,
		"dry_run": dry_run,
		"planned": planned,
		"already_wired": already_wired,
		"unmapped_components": unmapped,
	}
