if (!frappe.__zip_export_patched) {
	frappe.__zip_export_patched = true;

	const ZIP_ENABLED_DOCTYPES = ["Payroll Entry", "Salary Slip", "Attendance"];

	frappe.require("data_import_tools.bundle.js", () => {
		let original_make_dialog = frappe.data_import.DataExporter.prototype.make_dialog;
		frappe.data_import.DataExporter.prototype.make_dialog = function () {
			original_make_dialog.apply(this, arguments);
			if (ZIP_ENABLED_DOCTYPES.includes(this.doctype)) {
				let file_type_field = this.dialog.get_field("file_type");
				file_type_field.df.options = ["Excel", "CSV", "Zip File"];
				file_type_field.set_options();
			}
		};

		let original_export_records = frappe.data_import.DataExporter.prototype.export_records;
		frappe.data_import.DataExporter.prototype.export_records = function () {
			let values = this.dialog.get_values();

			if (ZIP_ENABLED_DOCTYPES.includes(this.doctype) && values.file_type === "Zip File") {
				let multicheck_fields = this.dialog.fields
					.filter((df) => df.fieldtype === "MultiCheck")
					.map((df) => df.fieldname);

				let doctype_field_map = Object.assign({}, values);
				for (let key in doctype_field_map) {
					if (!multicheck_fields.includes(key)) {
						delete doctype_field_map[key];
					}
				}

				let filters = null;
				if (values.export_records === "by_filter") {
					filters = this.get_filters();
				}

				open_url_post("/api/method/tsi_custom.api.export_as_zip", {
					doctype: this.doctype,
					export_records: values.export_records,
					export_fields: doctype_field_map,
					export_filters: filters,
				});
				return;
			}

			original_export_records.apply(this, arguments);
		};
	});
}
