"""A4: signed Settings deletion authorization and executable Mapping DOM."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application.settings import (
    SettingsSaveOutcome, load_settings, plan_settings_deletions, save_settings,
)
from literature_monitor.config import (
    AccessService, JournalConfig, PublisherConfig, render_settings_list_text,
)
from literature_monitor.web.app import create_app
from test_access_service_schema import A, B, P1, P2, current_mapping_document


def stamp(path: Path):
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns


def test_service_deletion_preview_lists_only_publishers_actually_unassigned(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    before = load_settings(config).draft
    moved = before.publishers[0].model_copy(update={"access_service_id": spare.id})
    candidate = replace(before, access_services=(spare,), publishers=(moved, before.publishers[1]))
    plan = plan_settings_deletions(before, candidate)
    assert [item.id for item in plan.services] == [service.id]
    assert plan.service_members == ((service.id, ()),)
    assert not plan.publishers
    assert save_settings(config, candidate).outcome is SettingsSaveOutcome.INVALID_DRAFT


def prepared(tmp_path):
    config, path, journal, publisher, service = current_mapping_document(tmp_path)
    spare = AccessService.create("Unused")
    journals = (journal, JournalConfig(name="Annals", issn_l=B, publisher_id=P2, group="Other"))
    publishers = (publisher, PublisherConfig(name="Publisher Two", publisher_id=P2,
                                            publisher_url="https://second.example/manual"))
    path.write_text(render_settings_list_text(
        path.read_text(), journals, publishers, access_services=(service, spare), path=path,
    ), encoding="utf-8")
    return config, path, service, spare
