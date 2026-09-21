from datetime import date

import pytest

from literature_monitor.date_range import (
    DEFAULT_WINDOW_DAYS,
    DateRangeError,
    DateRangeSpec,
    ResolvedDateRange,
    resolve_date_range,
)


TODAY = date(2026, 9, 21)


@pytest.mark.parametrize(
    ("window_days", "expected_from"),
    [
        (DEFAULT_WINDOW_DAYS, date(2026, 9, 8)),
        (1, date(2026, 9, 21)),
        (30, date(2026, 8, 23)),
    ],
)
def test_resolve_rolling_window(window_days: int, expected_from: date) -> None:
    resolved = resolve_date_range(
        DateRangeSpec(window_days=window_days),
        today=TODAY,
    )

    assert resolved == ResolvedDateRange(
        from_date=expected_from,
        to_date=TODAY,
    )


def test_resolve_fixed_range_is_inclusive() -> None:
    resolved = resolve_date_range(
        DateRangeSpec(
            from_date=date(2026, 9, 1),
            to_date=date(2026, 9, 21),
        ),
        today=TODAY,
    )

    assert resolved == ResolvedDateRange(
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 21),
    )
    assert (resolved.to_date - resolved.from_date).days + 1 == 21


def test_resolve_window_anchored_at_start() -> None:
    resolved = resolve_date_range(
        DateRangeSpec(
            from_date=date(2026, 9, 1),
            window_days=21,
        ),
        today=TODAY,
    )

    assert resolved == ResolvedDateRange(
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 21),
    )


def test_resolve_window_anchored_at_end() -> None:
    resolved = resolve_date_range(
        DateRangeSpec(
            to_date=date(2026, 9, 21),
            window_days=14,
        ),
        today=TODAY,
    )

    assert resolved == ResolvedDateRange(
        from_date=date(2026, 9, 8),
        to_date=date(2026, 9, 21),
    )


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (
            DateRangeSpec(to_date=date(2026, 2, 28), window_days=2),
            ResolvedDateRange(
                from_date=date(2026, 2, 27),
                to_date=date(2026, 2, 28),
            ),
        ),
        (
            DateRangeSpec(from_date=date(2028, 2, 28), window_days=2),
            ResolvedDateRange(
                from_date=date(2028, 2, 28),
                to_date=date(2028, 2, 29),
            ),
        ),
        (
            DateRangeSpec(to_date=date(2026, 4, 1), window_days=2),
            ResolvedDateRange(
                from_date=date(2026, 3, 31),
                to_date=date(2026, 4, 1),
            ),
        ),
        (
            DateRangeSpec(from_date=date(2026, 12, 31), window_days=2),
            ResolvedDateRange(
                from_date=date(2026, 12, 31),
                to_date=date(2027, 1, 1),
            ),
        ),
    ],
)
def test_resolver_uses_standard_date_arithmetic(
    spec: DateRangeSpec,
    expected: ResolvedDateRange,
) -> None:
    assert resolve_date_range(spec, today=TODAY) == expected


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (
            DateRangeSpec(
                from_date=date(2026, 9, 22),
                to_date=date(2026, 9, 21),
            ),
            "from_date must not be after to_date",
        ),
        (
            DateRangeSpec(
                from_date=date(2026, 9, 1),
                to_date=date(2026, 9, 21),
                window_days=21,
            ),
            "cannot all be specified",
        ),
        (DateRangeSpec(window_days=0), "at least 1"),
        (DateRangeSpec(window_days=-1), "at least 1"),
        (
            DateRangeSpec(from_date=date(2026, 9, 1)),
            "must be combined",
        ),
        (
            DateRangeSpec(to_date=date(2026, 9, 21)),
            "must be combined",
        ),
    ],
)
def test_resolver_rejects_invalid_specs(
    spec: DateRangeSpec,
    message: str,
) -> None:
    with pytest.raises(DateRangeError, match=message):
        resolve_date_range(spec, today=TODAY)


def test_resolved_range_rejects_reversed_dates() -> None:
    with pytest.raises(DateRangeError, match="from_date must not be after to_date"):
        ResolvedDateRange(
            from_date=date(2026, 9, 22),
            to_date=date(2026, 9, 21),
        )
