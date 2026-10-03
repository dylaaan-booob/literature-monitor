"""Frozen browser plans and authenticated download evidence (SPEC §§36.10–11).

These primitives do not drive the acquisition coordinator or authorize writes.
All transport URLs and local paths are ephemeral and excluded from repr.
"""

from dataclasses import dataclass, field
from enum import Enum
import ipaddress
import json
import re
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

from .application.acquisition import AcquisitionTask
from .identifiers import normalize_doi
from .web.browser_handoff import AuthenticatedBrowserEvent, MAX_PAYLOAD_BYTES


class BrowserEvidenceError(ValueError):
    """Sanitized boundary failure; never include browser input in messages."""


class PublisherState(str, Enum):
    ACCESSIBLE = 'accessible'
    HUMAN_REQUIRED = 'human_required'
    EXHAUSTED = 'exhausted'


def safe_runtime_url(value: object) -> str:
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
            raise ValueError
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError
        if parts.hostname.lower() == 'localhost' or parts.hostname.lower().endswith('.localhost'):
            raise ValueError
        try:
            if not ipaddress.ip_address(parts.hostname).is_global:
                raise ValueError
        except ValueError:
            # A hostname is permitted; a syntactically valid private IP is not.
            if re.fullmatch(r'[0-9.]+|.*:.*', parts.hostname):
                raise ValueError
        _ = parts.port
        return value
    except (ValueError, TypeError):
        raise BrowserEvidenceError('Unsafe browser URL.') from None


def frozen_identity(task: AcquisitionTask) -> None:
    if not isinstance(task, AcquisitionTask) or not isinstance(task.task_id, UUID):
        raise BrowserEvidenceError('Ineligible frozen browser task.')
    if not isinstance(task.doi, str) or len(task.doi) > 200 or normalize_doi(task.doi) != task.doi or not re.fullmatch(r'10\.\d{4,9}/[^\s]+', task.doi):
        raise BrowserEvidenceError('Invalid frozen DOI.')


def ebsco_record_url(value: object) -> str:
    url = safe_runtime_url(value)
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'research.ebsco.com'
            or not re.fullmatch(r'/c/[a-zA-Z0-9_-]+/search/details/[a-zA-Z0-9_-]+', parts.path)):
        raise BrowserEvidenceError('Invalid provider record context.')
    return url


@dataclass(frozen=True)
class BrowserNavigationPlan:
    task_id: UUID
    doi: str
    direct_url: str = field(repr=False)

    def message(self) -> dict:
        """Only frozen navigation identity, without Zotero/Paper credentials."""
        return {'task_id': str(self.task_id), 'doi': self.doi,
                'direct_url': self.direct_url}


def navigation_plan(task: AcquisitionTask) -> BrowserNavigationPlan:
    frozen_identity(task)
    target = safe_runtime_url('https://doi.org/' + quote(task.doi, safe='/'))
    return BrowserNavigationPlan(task.task_id, task.doi, target)


def xmu_fallback_url(task: AcquisitionTask, publisher_state: PublisherState) -> str:
    frozen_identity(task)
    if publisher_state is not PublisherState.EXHAUSTED:
        raise BrowserEvidenceError('Institutional fallback is not eligible.')
    return 'https://resolver.ebsco.com/c/45yels/result?' + urlencode({
        'rft_id': 'info:doi/' + task.doi, 'x-opid': '45yels',
        'customer': 's1215021', 'group': 'main', 'profile': 'ftf',
    })


@dataclass(frozen=True)
class BrowserDownloadEvidence:
    task_id: UUID
    tab_binding: str = field(repr=False)
    doi: str
    download_id: int
    route: str
    ownership: str
    navigation_url: str = field(repr=False)
    path: str = field(repr=False)
    url: str | None = field(repr=False)
    final_url: str | None = field(repr=False)
    referrer: str = field(repr=False)
    mime: str
    total_bytes: int
    file_size: int
    category: str
    navigation_time: int
    start_time: int
    observed_doi: str | None = None
    transport_kind: str = 'http'
    download_origin: str | None = field(default=None, repr=False)
    provider_record_url: str | None = field(default=None, repr=False)
    attribution: str | None = None
    action_time: int | None = None


DOWNLOAD_FIELDS = frozenset({
    'doi', 'download_id', 'route', 'ownership', 'navigation_url', 'path', 'url',
    'final_url', 'referrer', 'mime', 'total_bytes', 'file_size', 'category',
    'navigation_time', 'start_time', 'state', 'observed_doi',
})
BLOB_DOWNLOAD_FIELDS = DOWNLOAD_FIELDS | {'transport_kind', 'download_origin', 'provider_record_url'}
PROVIDER_ACTION_FIELDS = BLOB_DOWNLOAD_FIELDS | {'attribution', 'action_time'}
ARM_WINDOW_MS = 10_000
PROVIDER_PREPARATION_WINDOW_MS = 120_000


def download_evidence(event: AuthenticatedBrowserEvent, task: AcquisitionTask,
                      claimed_tab_binding: str) -> BrowserDownloadEvidence:
    """Parse only an A5-authenticated envelope against the caller's frozen task.

    A8 must supply the verified registry binding, not one supplied by a page.
    Browser completion and metadata are claims; staging still verifies the file.
    """
    try:
        frozen_identity(task)
        p = event.payload
        if event.task_id != task.task_id or event.tab_binding != claimed_tab_binding or not re.fullmatch(r'tab-\d{1,16}', claimed_tab_binding):
            raise ValueError
        if event.event_type != 'download_candidate' or type(p) is not dict:
            raise ValueError
        blob = p.get('transport_kind') == 'blob'
        provider_action = blob and p.get('attribution') == 'ebsco_pdf_action'
        allowed_fields = PROVIDER_ACTION_FIELDS if provider_action else BLOB_DOWNLOAD_FIELDS if blob else DOWNLOAD_FIELDS
        if set(p) != allowed_fields:
            raise ValueError
        if len(json.dumps(p, ensure_ascii=False).encode()) > MAX_PAYLOAD_BYTES or p['doi'] != task.doi or p['state'] != 'complete':
            raise ValueError
        observed_doi = normalize_doi(p['observed_doi']) if p['observed_doi'] is not None else None
        if p['observed_doi'] is not None and observed_doi != task.doi:
            raise ValueError
        for key in ('download_id', 'total_bytes', 'file_size', 'navigation_time', 'start_time'):
            if type(p[key]) is not int or p[key] < 0:
                raise ValueError
        if p['file_size'] == 0:
            raise ValueError
        if provider_action:
            if (type(p['action_time']) is not int
                    or not 0 <= p['navigation_time'] <= p['action_time']
                    or not 0 <= p['start_time'] - p['action_time'] <= PROVIDER_PREPARATION_WINDOW_MS):
                raise ValueError
        elif not 0 <= p['start_time'] - p['navigation_time'] <= ARM_WINDOW_MS:
            raise ValueError
        if p['route'] not in {'direct', 'xmu'} or p['ownership'] not in ({'provider_action'} if provider_action else {'user_arm'} if blob else {'extension_id', 'task_navigation'}):
            raise ValueError
        if p['route'] == 'xmu' and p['category'] not in {'FullText', 'SmartLinks'}:
            raise ValueError
        if p['route'] == 'direct' and p['category'] != '':
            raise ValueError
        safe_runtime_url(p['navigation_url'])
        if not isinstance(p['referrer'], str):
            raise ValueError
        if p['referrer']:
            safe_runtime_url(p['referrer'])
        if blob:
            # The worker proves a same-tab generic arm or provider action; its HTTPS origin
            # crosses A5. Never accept a raw blob token or fake landing URL.
            origin = safe_runtime_url(p['download_origin'])
            navigation = urlsplit(p['navigation_url'])
            if (navigation.scheme != 'https' or origin != f'https://{navigation.netloc}'
                    or p['url'] is not None or p['final_url'] is not None
                    or p['referrer'] not in {'', p['navigation_url']}):
                raise ValueError
            if p['provider_record_url'] is not None:
                if p['route'] != 'xmu' or ebsco_record_url(p['provider_record_url']) != p['navigation_url']:
                    raise ValueError
            if p['route'] == 'xmu' and (p['provider_record_url'] is None or observed_doi != task.doi):
                raise ValueError
        else:
            for key in ('url', 'final_url'):
                safe_runtime_url(p[key])
            if p['navigation_url'] not in {p['url'], p['final_url'], p['referrer']}:
                raise ValueError
        if not isinstance(p['path'], str) or not 1 <= len(p['path']) <= 512 or '\x00' in p['path']:
            raise ValueError
        if not isinstance(p['mime'], str) or len(p['mime']) > 128:
            raise ValueError
        if provider_action and (p['route'] != 'xmu'
                or p['provider_record_url'] != ebsco_record_url(p['navigation_url'])
                or observed_doi != task.doi):
            raise ValueError
        return BrowserDownloadEvidence(event.task_id, event.tab_binding, task.doi,
            p['download_id'], p['route'], p['ownership'], p['navigation_url'], p['path'],
            p['url'], p['final_url'], p['referrer'], p['mime'], p['total_bytes'], p['file_size'],
            p['category'], p['navigation_time'], p['start_time'], observed_doi,
            transport_kind='blob' if blob else 'http', download_origin=p.get('download_origin'),
            provider_record_url=p.get('provider_record_url'), attribution=p.get('attribution'),
            action_time=p.get('action_time'))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise BrowserEvidenceError('Invalid task-bound download evidence.') from None
