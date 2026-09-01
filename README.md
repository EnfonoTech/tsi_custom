### TSI

Custom ERPNext App for Traffic Service International LLC

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site <site> install-app tsi_custom
bench --site <site> migrate
```

Run the `migrate` even on a fresh install: `install-app` has been seen to sync
zero DocTypes, and the `Component Allocation` field is a Table pointing at
`TSI Salary Allocation` — if that child DocType is missing the field is dead.
Confirm both landed before going further:

```bash
bench --site <site> execute frappe.client.get_count --kwargs "{'doctype': 'DocType', 'filters': {'name': 'TSI Salary Allocation'}}"
```

## Salary allocation

Per-component salary amounts on the Salary Structure Assignment (SSA),
changeable from a date, and read correctly by payroll for each period.

### How it works

Select a Salary Structure on an assignment and the **Component Allocation** grid
fills with every component that structure defines — earnings first, then
deductions. Each row holds the amount allocated to that employee:

| Salary Component | Type | Abbr | Amount | Formula Variable |
|---|---|---|---|---|
| Basic | Earning | B | 4,000 | `alloc_B` |
| Housing | Earning | H | 1,500 | `alloc_H` |
| Transport | Earning | T | 500 | `alloc_T` |
| PF | Deduction | PF | 300 | `alloc_PF` |

`Total Salary` is the sum of the Earning rows, kept read-only and recomputed on
every save.

On a submitted assignment two buttons appear: **Change Allocation** and
**Allocation History**.

**Change Allocation does not edit the assignment.** It creates the *next* one: a
new submitted SSA dated from the effective date, carrying everything else
forward — the other components, `base`, `variable`, `income_tax_slab`,
`currency`, `payroll_payable_account` and the payroll cost centres.

That is what makes payroll correct. hrms picks the assignment for a Salary Slip
with `from_date <= <slip start date> ORDER BY from_date DESC`, so a slip reads
the amounts in force for its own period, and regenerating an old slip after a
raise still reproduces its original figures.

A change effective mid-month applies from the **following** period, because a
slip resolves one assignment for the whole period. The dialog defaults to the
first of next month.

### How payroll reads the grid

A Salary Detail row only picks up a value if it is formula-based and its formula
names a variable in the evaluation namespace. That namespace is built by
`get_data_for_eval`, which merges `self._salary_structure_assignment` — and that
is a **flat row**, fetched with `frappe.db.get_value(..., "*")`
(`hrms/payroll/doctype/salary_slip/salary_slip.py:803`). **Child rows are
invisible to formulas.**

So each allocation row publishes a variable named `alloc_<ABBR>`, injected by
`tsi_custom/overrides/salary_slip.py`, which extends `get_data_for_eval` and
nothing else — it calls `super()` first, so core's assignment-selection logic is
untouched. This is a `override_doctype_class` on Salary Slip; **only one app can
override a given DocType class**, so check for a conflict before installing
alongside another payroll app.

### Adopting this on a site that already runs payroll

Do these in order. The wiring step refuses to run until step 1 is done, because
switching the structure over while an assignment has no grid would fail that
employee's next slip with a `Name error`.

**1. Backfill the grid onto existing submitted assignments.** They cannot grow
it by being re-saved — `validate` does not run on a submitted document.

```bash
bench --site <site> execute tsi_custom.salary_allocation.backfill_allocations --kwargs "{'salary_structure': 'TSI Monthly'}"
```

Review `planned`, then run again with `'dry_run': 0`.

Rows are created at **amount 0** on purpose. The real per-employee figures are
not knowable from the old assignment, and inventing them would pay the wrong
salary silently.

**2. Set the real amounts.** For each employee, open the assignment in force and
use **Change Allocation**, effective from your go-live date. That dates a fresh
assignment holding the correct figures.

**3. Wire the structure** (next section).

Do not regenerate a pre-adoption slip after wiring: those old assignments carry
zeros, so the slip would recompute to zero. Wiring changes how *any* slip
recomputes, which is why cut-over is dated.

### Required setup: wire the Salary Structure

Until each component row is formula-based and names its variable, the amounts
are stored and ignored — payroll keeps paying the old row amounts, silently.

Inspect what would change (nothing is written):

```bash
bench --site <site> execute tsi_custom.salary_allocation.wire_salary_structure_formulas --kwargs "{'salary_structure': 'TSI Monthly'}"
```

Check the report:

- `planned` — rows that will be switched to formula-based. `clears_amount` shows
  a fixed amount currently on the row; leaving one beside a formula is the
  classic double-pay bug, so it is set to 0.
- `already_wired` — idempotent, safe to re-run.
- `left_on_base` — rows whose formula is exactly `base` are **not** touched.
  That is core's own field, still used elsewhere in hrms; a site already driving
  Basic from it keeps doing so.
- `left_unmanaged` — income tax (`variable_based_on_taxable_salary`), flexible
  benefit and statistical rows are **never** wired, and get no allocation row
  either. hrms computes those itself; giving an income-tax row a formula stops
  it being treated as tax at all.

Then apply:

```bash
bench --site <site> execute tsi_custom.salary_allocation.wire_salary_structure_formulas --kwargs "{'salary_structure': 'TSI Monthly', 'dry_run': 0}"
```

Rows are written directly with `frappe.db.set_value`: `formula` and `condition`
are `allow_on_submit` on Salary Detail but `amount_based_on_formula` is not, and
cancelling the structure to amend it would break every assignment linked to it.

Verify on one employee before a payroll run — generate a slip in a period before
the change and one after, and check both component amounts.

### Notes

- **Adding a component to the structure later?** Run `backfill_allocations`
  again, set the amounts with Change Allocation, then re-run the wiring helper.
  Re-saving a *submitted* assignment does **not** add the row — `validate` never
  runs on a submitted document.
- Rows are added, never silently deleted. A component the structure no longer
  defines stays on the assignment and is reported instead, so the amount is not
  lost without someone seeing it.
- Two components whose abbreviations reduce to the same identifier are rejected —
  their amounts would otherwise overwrite each other in the formula namespace.
  A component listed as both an earning *and* a deduction in one structure is
  rejected too: one component gets one allocated amount, so it cannot be both.
- Allocation History badges a submitted-but-future assignment **Scheduled**, not
  Current — payroll is still using the earlier one until its date arrives.
- **Retro increments are allowed.** Dating a change before periods that are
  already paid is permitted, because "effective 1 March, approved in September"
  is normal. The submitted slips keep the amounts they were paid at, so **no
  arrears are created** — settle the difference separately, e.g. with an
  Additional Salary. The change reports which periods it reached behind.
- A change is refused only when it would be meaningless or ambiguous: an
  assignment already starts on that date, or a *later* assignment already exists
  (which would keep overriding it).
- Mid-year income tax openings (`taxable_earnings_till_date`,
  `tax_deducted_till_date`) are **not** carried onto the new assignment. They
  belong to the assignment that opened the payroll period; copying them into a
  later one inside the same period would re-arm a stale opening for every
  remaining slip.
- `Total Salary` is the contracted monthly allocation, **not** the paid gross. A
  slip prorates and rounds each component independently, so the two legitimately
  differ. Do not use it as gross in a report.
- Deliberately not included: the HR Suite Salary Breakup Table band lookup, and
  any in-place rewriting of a submitted assignment.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/tsi_custom
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
