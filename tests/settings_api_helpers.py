"""Build the existing Settings FormData payload from the UI Next JSON projection."""

def form(state):
    v = state["values"]
    data = {key: v[key] for key in (
        "name", "keyword_expression", "from_month", "to_month", "output_dir", "log_level",
        "monitor_revision_exists", "monitor_revision_digest", "journal_revision_exists",
        "journal_revision_digest", "legacy_journals", "transition_delay_seconds", "display_name",
        "institution_name", "institution_idp_entity_id",
    )}
    data.update(csrf_token=state["csrf_token"], settings_group=v["groups"])
    for key in ("automatic_attribute_release_consent", "auto_import_after_preparation"):
        data[key] = "1" if v[key] else ""
    for source, mapping in (
        ("journals", {"journal_name": "name", "journal_issns": "issns", "journal_group": "group",
                      "journal_publisher_id": "publisher_id", "journal_pending": "pending"}),
        ("publishers", {"publisher_name": "name", "publisher_id": "publisher_id",
                        "publisher_access_url": "publisher_url", "publisher_access_service_id": "access_service_id"}),
        ("access_services", {"service_id": "id", "service_name": "name",
                             "service_access_url": "access_url", "service_access_method": "access_method"}),
    ):
        for target, field in mapping.items():
            data[target] = [("1" if row[field] else "0") if field == "pending" else row[field]
                            for row in v[source]]
    return data
