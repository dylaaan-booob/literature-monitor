"""Timezone-safe Provider revision timestamps, independent of publication dates."""

from datetime import datetime, timezone
import re

_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)


def parse_revision_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str) and _TIMESTAMP.fullmatch(value):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("revision must be a timestamp with an explicit timezone")
    try:
        return value.astimezone(timezone.utc)
    except OverflowError as error:
        raise ValueError("revision is outside the supported timestamp range") from error
