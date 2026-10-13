from datetime import date
import pytest
from literature_monitor.date_range import DateRangeSpec, DateRangeError, ResolvedDateRange, resolve_date_range


@pytest.mark.parametrize('start,end,first,last', [
    ('2026-01','2026-01',date(2026,1,1),date(2026,1,31)),
    ('2024-02','2024-02',date(2024,2,1),date(2024,2,29)),
    ('2025-12','2026-02',date(2025,12,1),date(2026,2,28)),
])
def test_month_bounds(start,end,first,last):
    assert resolve_date_range(DateRangeSpec(start,end)) == ResolvedDateRange(first,last)


@pytest.mark.parametrize('start,end', [('', '2026-01'),('2026-02','2026-01'),('2026-13','2026-01')])
def test_invalid_month_selection(start,end):
    with pytest.raises(DateRangeError):
        resolve_date_range(DateRangeSpec(start,end))
