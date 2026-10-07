"""Read-only Publisher access projection derived from saved Journals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from literature_monitor.config import JournalConfig
from literature_monitor.openalex import (
    OpenAlexClient,
    ResolvedSource,
    SourceResolutionUnit,
    resolve_journal_sources_batched,
)
from literature_monitor.progress import ProgressCallback


@dataclass(frozen=True)
class PublisherAccessSite:
    source_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    homepage_url: str | None
    hostname: str | None
    journals: tuple[JournalConfig, ...]


@dataclass(frozen=True)
class PublisherAccessGroup:
    publisher_id: str | None
    publisher_name: str | None
    sites: tuple[PublisherAccessSite, ...]


@dataclass(frozen=True)
class PublisherAccessUnavailableJournal:
    journal: JournalConfig


@dataclass(frozen=True)
class PublisherAccessProjection:
    publishers: tuple[PublisherAccessGroup, ...]
    unavailable_journals: tuple[PublisherAccessUnavailableJournal, ...]


@dataclass
class _SourceAccumulator:
    source_id: str
    source_names: list[str] = field(default_factory=list)
    journals: list[JournalConfig] = field(default_factory=list)
    homepage_url: str | None = None
    hostname: str | None = None
    homepage_conflicted: bool = False

    def add(self, source: ResolvedSource, journal: JournalConfig) -> None:
        if source.display_name not in self.source_names:
            self.source_names.append(source.display_name)
        if journal not in self.journals:
            self.journals.append(journal)

        if self.homepage_conflicted or source.homepage_url is None:
            return
        if self.homepage_url is None:
            self.homepage_url = source.homepage_url
            self.hostname = source.homepage_hostname
            return
        if self.homepage_url != source.homepage_url:
            # 同一 Source 出现互相冲突的 presentation URL 时宁可降级为 unavailable，
            # 不把 provider 抖动误呈现成多个真实平台入口。
            self.homepage_url = None
            self.hostname = None
            self.homepage_conflicted = True


@dataclass
class _PublisherAccumulator:
    publisher_id: str | None
    publisher_names: list[str] = field(default_factory=list)
    sources: dict[str, _SourceAccumulator] = field(default_factory=dict)

    def add(self, source: ResolvedSource, journal: JournalConfig) -> None:
        if (
            source.host_organization_name is not None
            and source.host_organization_name not in self.publisher_names
        ):
            self.publisher_names.append(source.host_organization_name)
        source_entry = self.sources.setdefault(
            source.openalex_id,
            _SourceAccumulator(source_id=source.openalex_id),
        )
        source_entry.add(source, journal)


@dataclass
class _SiteAccumulator:
    source_ids: list[str] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    journals: list[JournalConfig] = field(default_factory=list)
    homepage_url: str | None = None
    hostname: str | None = None

    def add(self, source: _SourceAccumulator) -> None:
        if source.source_id not in self.source_ids:
            self.source_ids.append(source.source_id)
        for name in source.source_names:
            if name not in self.source_names:
                self.source_names.append(name)
        for journal in source.journals:
            if journal not in self.journals:
                self.journals.append(journal)
        if self.homepage_url is None and source.homepage_url is not None:
            self.homepage_url = source.homepage_url
            self.hostname = source.hostname


def _project_group(group: _PublisherAccumulator) -> PublisherAccessGroup:
    sites: dict[tuple[str, str], _SiteAccumulator] = {}
    for source in group.sources.values():
        site_key = (
            ("homepage", source.homepage_url)
            if source.homepage_url is not None
            else ("source", source.source_id)
        )
        site = sites.setdefault(
            site_key,
            _SiteAccumulator(
                homepage_url=source.homepage_url,
                hostname=source.hostname,
            ),
        )
        site.add(source)

    return PublisherAccessGroup(
        publisher_id=group.publisher_id,
        publisher_name=(
            group.publisher_names[0] if len(group.publisher_names) == 1 else None
        ),
        sites=tuple(
            PublisherAccessSite(
                source_ids=tuple(site.source_ids),
                source_names=tuple(site.source_names),
                homepage_url=site.homepage_url,
                hostname=site.hostname,
                journals=tuple(site.journals),
            )
            for site in sites.values()
        ),
    )


def project_publisher_access(
    units: Sequence[SourceResolutionUnit],
) -> PublisherAccessProjection:
    """Project Source resolution into deterministic Publisher access presentation data."""
    groups: dict[tuple[str, str], _PublisherAccumulator] = {}
    unavailable: list[PublisherAccessUnavailableJournal] = []

    for unit in units:
        source = unit.source
        if source is None:
            if not any(entry.journal == unit.journal for entry in unavailable):
                unavailable.append(
                    PublisherAccessUnavailableJournal(
                        journal=unit.journal,
                    )
                )
            continue

        # 有 stable host organization 才按 publisher 聚合；缺失时只按 Source 自身去重，
        # 绝不使用名称、DOI 或 hostname 猜 publisher identity。
        group_key = (
            ("publisher", source.host_organization)
            if source.host_organization is not None
            else ("source", source.openalex_id)
        )
        group = groups.setdefault(
            group_key,
            _PublisherAccumulator(publisher_id=source.host_organization),
        )
        group.add(source, unit.journal)

    return PublisherAccessProjection(
        publishers=tuple(_project_group(group) for group in groups.values()),
        unavailable_journals=tuple(unavailable),
    )


def resolve_publisher_access(
    client: OpenAlexClient,
    journals: Sequence[JournalConfig],
    *,
    progress_callback: ProgressCallback | None = None,
) -> PublisherAccessProjection:
    """Resolve saved Journals through the existing OpenAlex Source path, then project."""
    units = resolve_journal_sources_batched(
        client,
        journals,
        progress_callback=progress_callback,
    )
    return project_publisher_access(units)
