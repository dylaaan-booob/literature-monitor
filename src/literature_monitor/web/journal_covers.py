"""One in-memory Chrome cover batch; local asset operations are serialized."""

import base64
import binascii
from pathlib import Path
import secrets
import threading
import time

from literature_monitor.application.journal_covers import (
    MAX_IMAGE_BYTES, _has_record, _save_cover, cover_details, reset_covers, upload_cover,
)
from literature_monitor.url_safety import normalize_public_http_url

COVER_CAPABILITY = "journal-covers/v2"
MAX_IMAGE_BASE64 = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
FAILED_MESSAGE = "未获取到图片"
LEASE_SECONDS = 120


class JournalCoverSearch:
    def __init__(self, capture):
        self.capture = capture
        self._lock = threading.RLock()
        self._asset_lock = threading.Lock()
        self._uploading = False
        self._task = None

    def _release(self, task):
        if task['owner'] is not None:
            self.capture.release_browser_task('covers', task['owner'])
            task['owner'] = None

    def _expire(self):
        task = self._task
        if task and task['owner'] is not None and (
            time.monotonic() >= task['deadline'] or not self.capture.snapshot().connector_online
        ):
            if task['phase'] == 'Running':
                task.update(phase='Failed', message=FAILED_MESSAGE)
            self._release(task)

    def state(self):
        with self._lock:
            self._expire()
            online = self.capture.snapshot()
            state = dict(phase='Idle', request_id=None, total=0, completed=0,
                         saved=0, failed=0, results=[], message='', busy=self._uploading,
                         supported=online.connector_online and online.covers_capable)
            if self._task:
                state.update({key: self._task[key] for key in (
                    'phase', 'request_id', 'total', 'completed', 'saved', 'failed', 'message')})
                state['results'] = list(self._task['results'])
                state['busy'] = state['busy'] or self._task['owner'] is not None
            state['remaining'] = state['total'] - state['completed']
            stopped = not state['busy'] and state['phase'] != 'Running' and state['supported']
            state['can_continue'] = stopped and state['remaining'] > 0
            state['can_retry'] = stopped and state['failed'] > 0
            return state

    def _activate(self, task):
        if self.state()['busy']:
            raise ValueError('Connector is busy')
        if not self.state()['supported']:
            raise ValueError('Reload the Literature Monitor Connector to enable cover search')
        deadline = time.monotonic() + LEASE_SECONDS
        owner = self.capture.reserve_browser_task('covers', deadline=deadline) if task['completed'] < task['total'] else None
        if task['completed'] < task['total'] and owner is None:
            raise ValueError('Connector is busy')
        task.update(request_id=secrets.token_urlsafe(32), owner=owner, deadline=deadline,
                    claimed=False, phase='Running' if owner else 'Completed', message='')
        self._task = task
        return self.state()

    def start(self, list_path: Path, journals, issn_l=None):
        with self._lock:
            selected = [j for j in journals if j.issn_l == issn_l] if issn_l else [
                j for j in journals if not _has_record(list_path, j.issn_l)]
            if issn_l and not selected:
                raise ValueError('Save this Journal before searching')
            return self._activate(dict(list_path=list_path, journals=selected, missing_only=issn_l is None,
                                       total=len(selected), completed=0, saved=0, failed=0, results=[]))

    def resume(self, request_id, *, retry=False):
        with self._lock:
            state = self.state()
            if state['request_id'] != request_id or not state['can_retry' if retry else 'can_continue']:
                raise ValueError('No failed covers to retry' if retry else 'No remaining covers to continue')
            task = self._task
            if retry:
                failed = {r['issn_l'] for r in task['results'] if r['result'] == 'Failed'}
                selected = [j for j in task['journals'] if j.issn_l in failed]
                task = dict(list_path=task['list_path'], journals=selected, missing_only=task['missing_only'],
                            total=len(selected), completed=0, saved=0, failed=0, results=[])
            return self._activate(task)

    def claim(self):
        with self._lock:
            self._expire()
            task = self._task
            if not task or task['phase'] != 'Running' or task['claimed']:
                return None
            task['claimed'] = True
            return dict(request_id=task['request_id'], journals=[
                dict(issn_l=j.issn_l, name=j.name) for j in task['journals'][task['completed']:]])

    def _matching(self, request_id):
        self._expire()
        task = self._task
        return task if task and task['phase'] == 'Running' and task['claimed'] and task['request_id'] == request_id else None

    def authorize(self, request_id):
        with self._lock:
            task = self._matching(request_id)
            if task is None:
                return False
            deadline = time.monotonic() + LEASE_SECONDS
            if not self.capture.extend_browser_task('covers', task['owner'], deadline):
                return False
            task['deadline'] = deadline
            return True

    def result(self, request_id, issn_l, source_url, image_base64):
        with self._asset_lock:
            with self._lock:
                task = self._matching(request_id)
                if task is None or task['completed'] >= task['total']:
                    return None
                journal = task['journals'][task['completed']]
                if journal.issn_l != issn_l:
                    return None
            data = None
            try:
                if image_base64 and len(image_base64) <= MAX_IMAGE_BASE64 and normalize_public_http_url(source_url):
                    data = base64.b64decode(image_base64, validate=True)
                    if len(data) > MAX_IMAGE_BYTES:
                        data = None
            except (ValueError, binascii.Error):
                pass
            with self._lock:
                # Cancel may arrive while the image payload is being decoded.
                if self._matching(request_id) is not task:
                    return None
                outcome = 'Failed'
                try:
                    if task['missing_only'] and _has_record(task['list_path'], issn_l):
                        outcome = 'Skipped'
                    elif data is not None:
                        outcome = _save_cover(task['list_path'], issn_l, data,
                                              source_type='downloaded', source_url=source_url,
                                              missing_only=task['missing_only'])
                except (ValueError, OSError):
                    pass
                task['completed'] += 1
                task['failed' if outcome == 'Failed' else 'saved'] += 1
                task['results'].append(dict(issn_l=issn_l, result=outcome,
                                            **cover_details(task['list_path'], issn_l),
                                            status=FAILED_MESSAGE if outcome == 'Failed' else 'Saved' if outcome != 'Skipped' else 'Skipped'))
                return {'accepted': True}

    def upload(self, list_path, issn_l, data):
        with self._asset_lock:
            with self._lock:
                if self.state()['busy']:
                    raise ValueError('Cover search is running')
                owner = self.capture.reserve_browser_task('covers')
                if owner is None:
                    raise ValueError('Connector is busy')
                self._uploading = True
            try:
                return upload_cover(list_path, issn_l, data)
            finally:
                with self._lock:
                    self._uploading = False
                    self.capture.release_browser_task('covers', owner)

    def reset(self, list_path):
        if not self._asset_lock.acquire(blocking=False):
            raise ValueError('Cover assets are busy')
        try:
            with self._lock:
                if self.state()['busy']:
                    raise ValueError('Connector is busy')
                owner = self.capture.reserve_browser_task('covers')
                if owner is None:
                    raise ValueError('Connector is busy')
                try:
                    reset_covers(list_path)
                    self._task = None
                    return self.state()
                finally:
                    self.capture.release_browser_task('covers', owner)
        finally:
            self._asset_lock.release()

    def cancel(self, request_id):
        with self._lock:
            task = self._task
            if not task or task['request_id'] != request_id or task['phase'] != 'Running':
                return False
            task.update(phase='Cancelled', message='Cancelled')
            return True

    def finish(self, request_id):
        with self._lock:
            task = self._task
            if not task or task['request_id'] != request_id:
                return False
            if task['phase'] == 'Running':
                task.update(phase='Completed' if task['completed'] == task['total'] else 'Failed',
                            message='' if task['completed'] == task['total'] else FAILED_MESSAGE)
            self._release(task)
            return True
