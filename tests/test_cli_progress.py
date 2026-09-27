from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, Thread
from time import sleep

import pytest

from literature_monitor.cli_progress import _CliProgressRenderer
from literature_monitor.progress import ActivityKind, ActivityUpdate, ProgressEvent, ProgressStage


class DetectConcurrentStream:
    def __init__(self, tty):
        self.tty = tty
        self.parts = []
        self.writing = Lock()

    def isatty(self):
        return self.tty

    def write(self, value):
        assert self.writing.acquire(blocking=False), "concurrent stream write"
        try:
            midpoint = len(value) // 2
            self.parts.append(value[:midpoint])
            sleep(0.001)
            self.parts.append(value[midpoint:])
        finally:
            self.writing.release()

    def flush(self):
        assert self.writing.acquire(blocking=False), "flush raced with write"
        self.writing.release()

    def getvalue(self):
        return "".join(self.parts)


def concurrent_calls(*calls):
    barrier = Barrier(len(calls))
    errors = []
    def run(call):
        try:
            barrier.wait(timeout=5)
            call()
        except BaseException as error:
            errors.append(error)
    threads = [Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert not errors


@pytest.mark.parametrize("tty", [False, True])
def test_simultaneous_provider_callbacks_are_atomic_and_source_local(tty):
    stream = DetectConcurrentStream(tty)
    moment = datetime(2026, 9, 26, tzinfo=timezone.utc)
    def clock():
        nonlocal moment
        moment += timedelta(seconds=1)
        return moment
    renderer = _CliProgressRenderer(stream, show_run_stages=True, clock=clock)
    renderer(ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS))
    def report(source, total):
        for current in range(3):
            renderer(ProgressEvent(activity=ActivityUpdate(
                kind=ActivityKind.WORKING, source=source, operation=f"{source}_discovery",
                label=f"{source} records", current=current, total=total, unit="work",
            )))
    concurrent_calls(lambda: report("openalex", 20), lambda: report("crossref", 50))
    output = stream.getvalue()
    if tty:
        latest = output.split("\r\x1b[2K")[-1]
        assert "OpenAlex · openalex records · 2/20 works · ETA" in latest
        assert "Crossref · crossref records · 2/50 works · ETA" in latest
        renderer(ProgressEvent(stage=ProgressStage.MATCHING_LITERATURE))
        latest = stream.getvalue().split("\r\x1b[2K")[-1]
        assert "OpenAlex" not in latest and "Crossref" not in latest
    else:
        lines = output.splitlines(keepends=True)
        assert len(lines) == 7
        assert all(line.startswith("[progress] ") and line.endswith("\n") for line in lines)
        assert "\x1b" not in output and "\r" not in output
        for source, total, label in (("openalex", 20, "OpenAlex"), ("crossref", 50, "Crossref")):
            own = [line for line in lines if f"{label} ·" in line]
            assert len(own) == 3
            assert all(f"{source} records" in line and f"/{total} works" in line for line in own)
            assert all(not ("OpenAlex" in line and "Crossref" in line) for line in own)
    renderer.close()
    renderer.close()
    if tty:
        assert stream.getvalue().count("\r\x1b[2K\n") == 1


@pytest.mark.parametrize("tty", [False, True])
def test_callback_racing_with_close_and_repeated_stage_is_safe(tty):
    stream = DetectConcurrentStream(tty)
    renderer = _CliProgressRenderer(stream, show_run_stages=True)
    stage = ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS)
    renderer(stage)
    before = stream.getvalue()
    renderer(stage)
    assert stream.getvalue() == before
    event = ProgressEvent(activity=ActivityUpdate(
        kind=ActivityKind.WORKING, source="openalex", operation="works", label="Fetching works",
    ))
    concurrent_calls(lambda: renderer(event), renderer.close)
    renderer.close()
    closed = stream.getvalue()
    renderer(event)
    assert stream.getvalue() == closed
    if tty:
        assert closed.count("\r\x1b[2K\n") == 1


def test_tty_recovery_does_not_show_quiet_provider_pre_inactivity_eta():
    base = datetime(2026, 9, 26, tzinfo=timezone.utc)
    now = base
    stream = DetectConcurrentStream(True)
    renderer = _CliProgressRenderer(stream, show_run_stages=True, clock=lambda: now)
    renderer(ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS))
    def report(source, current):
        renderer(ProgressEvent(activity=ActivityUpdate(
            kind=ActivityKind.WORKING, source=source, operation="works",
            label=f"{source} records", current=current, total=10, unit="work",
        )))
    try:
        for current in range(3):
            now = base + timedelta(seconds=current + 1)
            for source in ("openalex", "crossref"):
                report(source, current)
        assert stream.getvalue().split("\r\x1b[2K")[-1].count("ETA 8s") == 2
        now = base + timedelta(seconds=63)
        inactive = renderer._state.snapshot(at=now, active=True)
        assert inactive.inactivity_warning
        assert all(activity.eta_seconds is None for activity in inactive.activities)
        now = base + timedelta(seconds=64)
        report("openalex", 3)
        latest = stream.getvalue().split("\r\x1b[2K")[-1]
        assert "OpenAlex" in latest and "Crossref" in latest
        assert "ETA " not in latest and "No recent activity" not in latest
        for current in (4, 5):
            now += timedelta(seconds=1)
            report("openalex", current)
        latest = stream.getvalue().split("\r\x1b[2K")[-1]
        assert latest.count("ETA 5s") == 1
        assert "ETA " not in latest.split("Crossref ·", 1)[1]
        quiet = next(activity for activity in renderer._state.snapshot(at=now, active=True).activities
                     if activity.source == "crossref")
        assert quiet.updated_at == base + timedelta(seconds=3)
    finally:
        renderer.close()
