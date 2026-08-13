import io
import zipfile

import frappe
from frappe.core.doctype.data_import.exporter import Exporter
from frappe.utils.xlsxutils import make_xlsx


@frappe.whitelist()
def export_as_zip(doctype, export_fields=None, export_records=None, export_filters=None):
	frappe.has_permission(doctype, "read", throw=True)

	export_fields = frappe.parse_json(export_fields)
	export_filters = frappe.parse_json(export_filters)
	export_data = export_records != "blank_template"

	exporter = Exporter(
		doctype,
		export_fields=export_fields,
		export_data=export_data,
		export_filters=export_filters,
		file_type="Excel",
		export_page_length=5 if export_records == "5_records" else None,
	)

	xlsx_content = make_xlsx(exporter.get_csv_array_for_export(), doctype).getvalue()

	zip_buffer = io.BytesIO()
	with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
		zf.writestr(f"{frappe.scrub(doctype)}.xlsx", xlsx_content)

	frappe.response["filename"] = f"{frappe.scrub(doctype)}.zip"
	frappe.response["filecontent"] = zip_buffer.getvalue()
	frappe.response["type"] = "download"
