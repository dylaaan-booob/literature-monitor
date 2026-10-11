"""Isolated saved mapping and Kept Papers for Preparation protocol regressions."""
from literature_monitor.application.settings import load_settings
from literature_monitor.config import load_config, render_settings_list_text
from test_access_mapping_a4 import prepared as mapping_prepared
from test_batch_import import edit, paper


def prepared(tmp_path):
    config, path, first, second = mapping_prepared(tmp_path)
    draft = load_settings(config).draft
    workspace = load_config(config).output_dir
    assert workspace.is_relative_to(tmp_path.resolve())
    second = second.model_copy(update={"name": "Second Service"})
    publishers = (draft.publishers[0], draft.publishers[1].model_copy(update={"access_service_id": second.id}))
    path.write_text(render_settings_list_text(path.read_text(), draft.journals, publishers,
                                             access_services=(first, second), path=path))
    for index, journal in enumerate(draft.journals):
        _, target = paper(workspace, f"preparation-kept-{index}")
        edit(target, journal=journal.name, journal_issns=[journal.issn_l])
    return config, path, first, second
