"""Persistent date-range policy and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


DEFAULT_WINDOW_DAYS = 14


class DateRangeError(ValueError):
    """Invalid date policy or resolved range."""

    def __init__(self, message: str, *, field: str = "date_range") -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class DateRangeSpec:
    """An unresolved date policy."""

    from_date: date | None = None
    to_date: date | None = None
    window_days: int | None = None


@dataclass(frozen=True)
class ResolvedDateRange:
    """An inclusive runtime date range."""

    from_date: date
    to_date: date

    def __post_init__(self) -> None:
        if self.from_date > self.to_date:
            raise DateRangeError(
                "from_date must not be after to_date",
                field="from_date",
            )


class _DateRangeForm(Enum):
    ROLLING = "rolling"
    FIXED = "fixed"
    FROM_WINDOW = "from_window"
    TO_WINDOW = "to_window"


def _date_range_form(spec: DateRangeSpec) -> _DateRangeForm:
    if spec.window_days is not None and spec.window_days < 1:
        raise DateRangeError(
            "window_days must be at least 1",
            field="window_days",
        )

    has_from = spec.from_date is not None
    has_to = spec.to_date is not None
    has_window = spec.window_days is not None
    field_count = sum((has_from, has_to, has_window))

    if field_count == 0:
        raise DateRangeError("date policy must define a range")

    if field_count == 3:
        raise DateRangeError(
            "from_date, to_date, and window_days cannot all be specified"
        )

    if has_from and has_to:
        if spec.from_date > spec.to_date:
            raise DateRangeError(
                "from_date must not be after to_date",
                field="from_date",
            )
        return _DateRangeForm.FIXED

    if has_from and has_window:
        return _DateRangeForm.FROM_WINDOW

    if has_to and has_window:
        return _DateRangeForm.TO_WINDOW

    if has_window:
        return _DateRangeForm.ROLLING

    field = "from_date" if has_from else "to_date"
    raise DateRangeError(
        f"{field} must be combined with another date field",
        field=field,
    )


def validate_date_range_spec(spec: DateRangeSpec) -> None:
    """Validate an unresolved date policy without resolving rolling dates."""

    _date_range_form(spec)


def resolve_date_range(
    spec: DateRangeSpec,
    *,
    today: date,
) -> ResolvedDateRange:
    """Resolve an inclusive date range using the caller-supplied current date."""

    form = _date_range_form(spec)

    if form is _DateRangeForm.FIXED:
        assert spec.from_date is not None
        assert spec.to_date is not None
        return ResolvedDateRange(from_date=spec.from_date, to_date=spec.to_date)

    assert spec.window_days is not None
    try:
        offset = timedelta(days=spec.window_days - 1)
        if form is _DateRangeForm.ROLLING:
            return ResolvedDateRange(from_date=today - offset, to_date=today)
        if form is _DateRangeForm.FROM_WINDOW:
            assert spec.from_date is not None
            return ResolvedDateRange(
                from_date=spec.from_date,
                to_date=spec.from_date + offset,
            )

        assert spec.to_date is not None
        return ResolvedDateRange(
            from_date=spec.to_date - offset,
            to_date=spec.to_date,
        )
    except OverflowError as error:
        raise DateRangeError(
            "date range exceeds supported datetime.date bounds",
            field="window_days",
        ) from error
