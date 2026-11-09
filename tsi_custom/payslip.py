# apps/tsi_custom/tsi_custom/payslip.py
"""Data helpers for the TSI Salary Slip print format.

Salary Slip carries totals (``leave_without_pay``, ``absent_days``,
``payment_days``) but no per-type breakdown of the leave taken in its period.
Its ``leave_details`` table is a *balance* snapshot (allocated / used /
available for the whole allocation period), which is not what the client asked
to see. :func:`get_leave_taken` rebuilds the breakdown from approved Leave
Applications, counting only the days that fall inside the slip's period.
"""

import frappe
from frappe import _
from frappe.query_builder import Criterion
from frappe.utils import flt, getdate
from hrms.hr.doctype.leave_application.leave_application import get_number_of_leave_days


def clip_to_period(from_date, to_date, period_start, period_end):
	"""Return the part of ``from_date..to_date`` inside the period, or None."""
	start = max(getdate(from_date), getdate(period_start))
	end = min(getdate(to_date), getdate(period_end))
	if start > end:
		return None
	return start, end


def get_leave_taken(salary_slip):
	"""Leave taken in the slip's period, one row per Leave Type.

	Returns ``[{"leave_type", "days", "pay_note"}]`` sorted by leave type.
	``pay_note`` is "Unpaid", "75% paid" etc., or "" for fully paid leave.
	"""
	if isinstance(salary_slip, str):
		salary_slip = frappe.get_doc("Salary Slip", salary_slip)

	start, end = salary_slip.start_date, salary_slip.end_date
	la = frappe.qb.DocType("Leave Application")
	applications = (
		frappe.qb.from_(la)
		.select(la.leave_type, la.from_date, la.to_date, la.half_day, la.half_day_date)
		.where(
			Criterion.all(
				[
					la.employee == salary_slip.employee,
					la.docstatus == 1,
					la.status == "Approved",
					la.from_date <= end,
					la.to_date >= start,
				]
			)
		)
		.run(as_dict=True)
	)

	days_by_type = {}
	for app in applications:
		clipped = clip_to_period(app.from_date, app.to_date, start, end)
		if not clipped:
			continue
		half_day = 0
		if app.half_day:
			if app.half_day_date:
				half_day = int(clipped[0] <= getdate(app.half_day_date) <= clipped[1])
			else:
				half_day = int(clipped[0] == clipped[1])
		days = get_number_of_leave_days(
			salary_slip.employee, app.leave_type, clipped[0], clipped[1], half_day, app.half_day_date
		)
		days_by_type[app.leave_type] = days_by_type.get(app.leave_type, 0) + flt(days)

	if not days_by_type:
		return []

	pay_terms = {
		d.name: d
		for d in frappe.get_all(
			"Leave Type",
			filters={"name": ["in", list(days_by_type)]},
			fields=["name", "is_lwp", "is_ppl", "fraction_of_daily_salary_per_leave"],
		)
	}
	return [
		{"leave_type": leave_type, "days": days, "pay_note": pay_note(pay_terms.get(leave_type))}
		for leave_type, days in sorted(days_by_type.items())
		if days
	]


def pay_note(leave_type):
	"""Short pay label for a Leave Type row from ``frappe.get_all``."""
	if not leave_type:
		return ""
	if leave_type.is_lwp:
		return _("Unpaid")
	if leave_type.is_ppl:
		return _("{0}% paid").format(f"{flt(leave_type.fraction_of_daily_salary_per_leave) * 100:g}")
	return ""
