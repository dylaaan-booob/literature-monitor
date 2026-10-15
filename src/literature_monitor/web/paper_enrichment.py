"""Explicit Paper enrichment with immediate saving; browser ownership stays in CaptureCoordinator."""
from __future__ import annotations

import secrets
import threading
import time
from urllib.parse import quote
from uuid import UUID
from pathlib import Path
from literature_monitor.models import parse_publication_date

from literature_monitor.application.paper_enrichment import read_enrichment_target, save_enrichment
from literature_monitor.identifiers import normalize_doi
from literature_monitor.safe_write import TextWriteCommittedError, ContentChangedError, CompareReadError

ENRICHMENT_CAPABILITY = 'paper-enrichment/v2'


def publication_date_candidate(item):
    """Preserve the precision of parsed parts from Zotero's sandbox."""
    raw, parts = item.get('date'), item.get('date_parts')
    if not isinstance(raw, str) or not raw.strip() or not isinstance(parts, dict):
        return None
    if 'year' not in parts or set(parts) - {'year', 'month', 'day'}:
        return None
    if any(type(value) is not int for value in parts.values()) or ('day' in parts and 'month' not in parts):
        return None
    try:
        value = f"{parts['year']:04d}"
        for component in ('month', 'day'):
            if component in parts:
                value += f"-{parts[component]:02d}"
        return parse_publication_date(value)
    except (ValueError, OverflowError):
        return None


def authors_candidate(item):
    creators = item.get('creators')
    if not isinstance(creators, list):
        return None
    names = []
    for creator in creators:
        if not isinstance(creator, dict):
            return None
        if creator.get('creatorType') != 'author':
            continue
        mode = creator.get('fieldMode', 0)
        if type(mode) is not int or mode not in {0, 1}:
            return None
        parts = [creator.get('lastName', '')] if mode == 1 else [
            creator.get('firstName', ''), creator.get('lastName', '')]
        if any(not isinstance(part, str) for part in parts):
            return None
        name = ' '.join(part.strip() for part in parts if part.strip())
        # These characters cannot safely appear in a Paper Author wikilink alias.
        if not name or any(character in name for character in (']', '\r', '\n', '\0')):
            return None
        names.append(name)
    return names or None


class PaperEnrichment:
    def __init__(self, capture):
        self.capture = capture
        self._lock = threading.RLock()
        self._task = None
        self._completed_papers: set[UUID] = set()

    def _release(self, task):
        self.capture.release_browser_task('enrichment', task['owner'])

    def _expire(self):
        task = self._task
        if task and task['phase'] == 'Retrieving':
            online = self.capture.snapshot()
            if time.monotonic() >= task['deadline'] or not online.connector_online or not online.enrichment_capable:
                task.update(phase='Failed', message='Connector disconnected or retrieval timed out')
                self._release(task)

    def state(self, paper_id: UUID, request_id: str | None = None):
        with self._lock:
            self._expire()
            task = self._task
            online = self.capture.snapshot()
            result = {'phase': 'Idle', 'connector_online': online.connector_online,
                      'supported': online.enrichment_capable, 'message': '', 'request_id': None,
                      'completed_in_session': paper_id in self._completed_papers}
            if task and task['target'].paper_id == paper_id and (request_id is None or task['request_id'] == request_id):
                result.update(phase=task['phase'], message=task['message'], request_id=task['request_id'])
                result['missing_abstract'] = task['target'].missing_abstract and 'abstract' not in task['saved']
                saved_date = task['saved'].get('publication_date')
                result['missing_date'] = task['target'].missing_date if saved_date is None else len(saved_date) == 7
                result['missing_authors'] = task['target'].missing_authors and 'authors' not in task['saved']
                if task['phase'] == 'Completed':
                    result['saved'] = task['saved']
            return result

    def start(self, workspace: Path, paper_id: UUID):
        target = read_enrichment_target(workspace, paper_id)
        with self._lock:
            if paper_id in self._completed_papers:
                raise ValueError('Enrichment was already saved for this Paper in the current Web session')
            self._expire()
            online = self.capture.snapshot()
            if not online.connector_online or not online.enrichment_capable:
                raise ValueError('Literature Monitor Connector is offline or does not support Paper enrichment')
            deadline = time.monotonic() + 75
            owner = self.capture.reserve_browser_task('enrichment', deadline=deadline)
            if owner is None:
                raise ValueError('Connector is busy')
            self._task = dict(target=target, owner=owner, request_id=secrets.token_urlsafe(32),
                              invocation_id=secrets.token_urlsafe(32), phase='Retrieving', message='Retrieving missing fields…',
                              doi_url=f'https://doi.org/{quote(target.doi, safe="/")}',
                              claimed=False, deadline=deadline, manual=False, saved={})
            return self.state(paper_id)

    def claim(self):
        with self._lock:
            self._expire()
            task = self._task
            if not task or task['phase'] != 'Retrieving' or task['claimed']:
                return None
            task['claimed'] = True
            return {key: task[key] for key in ('request_id', 'invocation_id', 'doi_url')}

    def _matching(self, identity):
        self._expire()
        task = self._task
        return task if task and task['phase'] == 'Retrieving' and task['claimed'] and all(
            task[key] == identity.get(key) for key in ('request_id', 'invocation_id', 'doi_url')) else None

    def authorize(self, identity, waiting_for_user=False):
        with self._lock:
            task = self._matching(identity)
            if task and waiting_for_user and not task['manual']:
                deadline = time.monotonic() + 600
                if not self.capture.extend_browser_task('enrichment', task['owner'], deadline):
                    return False
                task.update(manual=True, deadline=deadline,
                            message='Complete authentication or the challenge in Chrome')
            return task is not None

    def result(self, workspace, identity, items, error=None):
        with self._lock:
            task = self._matching(identity)
            if task is None:
                return None
            candidate = {}
            try:
                if workspace != task['target'].workspace:
                    raise ValueError('Configured Workspace changed')
                if error:
                    raise ValueError(error)
                if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
                    raise ValueError('Translator must return exactly one literature Item')
                item = items[0]
                if item.get('item_type') in {None, 'multiple', 'webpage', 'attachment', 'note'}:
                    raise ValueError('Translator did not return a literature Item')
                if normalize_doi(item.get('doi')) != task['target'].doi:
                    raise ValueError('Translator DOI is missing or does not match this Paper')
                abstract = item.get('abstract')
                if task['target'].missing_abstract and isinstance(abstract, str) and abstract.strip():
                    candidate['abstract'] = abstract
                if task['target'].missing_date:
                    parsed_date = publication_date_candidate(item)
                    if parsed_date is not None and (task['target'].publication_date is None or len(parsed_date) == 10):
                        candidate['publication_date'] = parsed_date
                if task['target'].missing_authors:
                    authors = authors_candidate(item)
                    if authors:
                        candidate['authors'] = authors
                if not candidate:
                    raise ValueError('Translator returned no usable missing fields')
                task['phase'] = 'Saving'
                save_enrichment(task['target'], candidate.get('abstract'),
                                candidate.get('publication_date'),
                                authors=candidate.get('authors'))
                task.update(phase='Completed', saved=candidate,
                            message='Enrichment saved')
            except TextWriteCommittedError as exc:
                task.update(phase='Completed', saved=candidate,
                            message=f'Enrichment saved; filesystem sync failed: {exc}')
            except (ValueError, TypeError, OSError, ContentChangedError, CompareReadError) as exc:
                task.update(phase='Failed', message=str(exc))
            finally:
                if task['phase'] == 'Completed':
                    self._completed_papers.add(task['target'].paper_id)
                self._release(task)
            return {'accepted': True, 'phase': task['phase']}

    def cancel(self, paper_id, request_id):
        with self._lock:
            task = self._task
            if (not task or task['target'].paper_id != paper_id
                    or task['request_id'] != request_id or task['phase'] != 'Retrieving'):
                return False
            self._release(task)
            self._task = None
            return True
