"""Explicit publication-month selection; historical snapshots retain day ranges."""
from dataclasses import dataclass
from datetime import date
from calendar import monthrange
import re


class DateRangeError(ValueError):
    def __init__(self, message: str, *, field: str = "date_range") -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class DateRangeSpec:
    from_month: str = ""
    to_month: str = ""


@dataclass(frozen=True)
class ResolvedDateRange:
    from_date: date
    to_date: date

    def __post_init__(self) -> None:
        if self.from_date > self.to_date:
            raise DateRangeError("from_date must not be after to_date", field="from_date")


def _month(value: str, field: str) -> date:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
            raise ValueError
        return date.fromisoformat(value + "-01")
    except ValueError:
        raise DateRangeError("Select a valid publication month (YYYY-MM).", field=field) from None


def validate_date_range_spec(spec: DateRangeSpec) -> None:
    start, end = _month(spec.from_month, "from_month"), _month(spec.to_month, "to_month")
    if start > end:
        raise DateRangeError("Start month must not be after end month.", field="from_month")


def resolve_date_range(spec: DateRangeSpec, *, today: date | None = None) -> ResolvedDateRange:
    validate_date_range_spec(spec)
    start, end = _month(spec.from_month, "from_month"), _month(spec.to_month, "to_month")
    return ResolvedDateRange(start, end.replace(day=monthrange(end.year, end.month)[1]))
