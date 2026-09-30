"""Thread boundaries are exercised with Events/Barriers, never external services."""

from dataclasses import FrozenInstanceError
import logging
import threading
from uuid import UUID

import pytest

from literature_monitor.application.acquisition import AcquisitionResult, AcquisitionOutcome as Outcome, AcquisitionRecovery as Recovery, AcquisitionStage as Stage
from literature_monitor.web import acquisition_coordinator as coordination
from literature_monitor.web.acquisition_coordinator import AcquisitionCoordinator, AcquisitionCoordinatorStatus as Status, AcquisitionStartOutcome as Start

ID = UUID(int=1)
OTHER = UUID(int=2)
SECRET = 'SENTINEL_COOKIE_CREDENTIAL https://proxy.example/?signature=SECRET'


class ControlledService:
    def __init__(self):
        self.entered=threading.Event()
        self.release=threading.Event()
        self.calls=[]
        self.error=None
        self.outcome=Outcome.SUCCEEDED
        self.recovery=Recovery.NONE
    def acquire(self,paper_id,*,stage_callback):
        self.calls.append((paper_id,threading.get_ident()))
        stage_callback(Stage.RESOLVING)
        self.entered.set()
        assert self.release.wait(5)
        if self.error:raise self.error
        return AcquisitionResult(paper_id,self.outcome,self.recovery)


@pytest.fixture
def fixture(monkeypatch):
    real_thread=threading.Thread
    workers=[]
    def thread(**kwargs):
        worker=real_thread(**kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(coordination.threading,'Thread',thread)
    service=ControlledService()
    coordinator=AcquisitionCoordinator(service)
    yield coordinator,service,workers,real_thread
    service.release.set()
    for worker in workers:
        if worker.ident is not None:worker.join(5)


def finished(workers):
    workers[-1].join(5)
    assert not workers[-1].is_alive()


def assert_sanitized(snapshot,caplog):
    assert SECRET not in repr(snapshot)+caplog.text
    assert 'proxy.example' not in repr(snapshot)+caplog.text
    assert not hasattr(snapshot,'started_at') and not hasattr(snapshot,'history')


def test_start_off_caller_thread_and_snapshot_observational(fixture):
    coordinator,service,workers,_=fixture
    initial=coordinator.snapshot()
    assert initial.status is Status.IDLE and service.calls==[] and workers==[]
    assert coordinator.start(ID).outcome is Start.STARTED
    assert service.entered.wait(5)
    snapshot=coordinator.snapshot()
    assert snapshot.status is Status.RUNNING and snapshot.paper_id==ID and snapshot.stage is Stage.RESOLVING
    assert service.calls==[(ID,workers[0].ident)] and service.calls[0][1]!=threading.get_ident()
    assert len(workers)==1
    assert coordinator.snapshot()==snapshot
    with pytest.raises(FrozenInstanceError):snapshot.stage=Stage.ATTACHING
    service.release.set();finished(workers)
    final=coordinator.snapshot()
    assert final.status is Status.FINISHED and final.stage is Stage.SUCCEEDED
    assert snapshot.status is Status.RUNNING  # Old snapshot remains immutable.


def test_simultaneous_second_start_busy_no_queue_or_replacement(fixture):
    coordinator,service,workers,real_thread=fixture
    assert coordinator.start(ID).outcome is Start.STARTED
    assert service.entered.wait(5)
    barrier=threading.Barrier(3)
    results=[]
    def caller():
        barrier.wait();results.append(coordinator.start(OTHER).outcome)
    callers=[real_thread(target=caller) for _ in range(2)]
    for caller_thread in callers:caller_thread.start()
    barrier.wait()
    for caller_thread in callers:caller_thread.join(5)
    assert results==[Start.ALREADY_RUNNING]*2
    assert len(workers)==1 and coordinator.snapshot().paper_id==ID
    service.release.set();finished(workers)
    assert [paper for paper,_ in service.calls]==[ID]
    assert coordinator.start(OTHER).outcome is Start.STARTED
    finished(workers)
    assert [paper for paper,_ in service.calls]==[ID,OTHER]


@pytest.mark.parametrize('outcome',[Outcome.SUCCEEDED,Outcome.PDF_ALREADY_ATTACHED,Outcome.UPLOAD_FAILURE,Outcome.CONFLICT])
def test_normal_terminal_paths_release_slot(fixture,outcome):
    coordinator,service,workers,_=fixture
    service.outcome=outcome;service.release.set()
    assert coordinator.start(ID).outcome is Start.STARTED
    finished(workers)
    final=coordinator.snapshot()
    assert final.status is Status.FINISHED and final.result.outcome is outcome and final.unexpected_error is None
    assert final.stage is (Stage.SUCCEEDED if outcome in (Outcome.SUCCEEDED,Outcome.PDF_ALREADY_ATTACHED) else Stage.FAILED)
    assert coordinator.start(OTHER).outcome is Start.STARTED
    finished(workers)
    assert len(workers)==2 and coordinator.snapshot().paper_id==OTHER


def test_auth_required_terminal_stage_preserved(fixture):
    coordinator,service,workers,_=fixture
    service.outcome=Outcome.AUTH_REQUIRED;service.recovery=Recovery.INSTITUTION_LOGIN;service.release.set()
    coordinator.start(ID);finished(workers)
    assert coordinator.snapshot().stage is Stage.WAITING_FOR_INSTITUTION_AUTH
    assert coordinator.snapshot().status is Status.FINISHED


@pytest.mark.parametrize('error',[RuntimeError(SECRET),KeyboardInterrupt(SECRET),SystemExit(SECRET)])
def test_unexpected_base_exception_sanitized_releases_slot(fixture,error,caplog):
    coordinator,service,workers,_=fixture
    caplog.set_level(logging.DEBUG)
    service.error=error;service.release.set();coordinator.start(ID);finished(workers)
    snapshot=coordinator.snapshot()
    assert snapshot.status is Status.FINISHED and snapshot.stage is Stage.FAILED
    assert snapshot.result is None and snapshot.unexpected_error.category==type(error).__name__
    assert_sanitized(snapshot,caplog)
    service.error=None
    assert coordinator.start(OTHER).outcome is Start.STARTED
    finished(workers)
    assert coordinator.snapshot().result.outcome is Outcome.SUCCEEDED


@pytest.mark.parametrize('boundary',['construct','start'])
def test_worker_start_failure_truthful_and_next_attempt_possible(fixture,monkeypatch,boundary,caplog):
    coordinator,service,workers,_=fixture
    factory=coordination.threading.Thread
    class Broken:
        def start(self):raise RuntimeError(SECRET)
    def broken(**kwargs):
        if boundary=='construct':raise RuntimeError(SECRET)
        return Broken()
    monkeypatch.setattr(coordination.threading,'Thread',broken)
    assert coordinator.start(ID).outcome is Start.START_FAILED
    snapshot=coordinator.snapshot()
    assert snapshot.status is Status.FINISHED and snapshot.stage is Stage.FAILED
    assert snapshot.result is None and service.calls==[]
    assert_sanitized(snapshot,caplog)
    monkeypatch.setattr(coordination.threading,'Thread',factory)
    service.release.set();assert coordinator.start(OTHER).outcome is Start.STARTED
    finished(workers)
    assert coordinator.snapshot().result.outcome is Outcome.SUCCEEDED


def test_custom_exception_class_name_not_exposed(fixture,caplog):
    coordinator,service,workers,_=fixture
    service.error=type('SENTINEL_SECRET_CLASS',(Exception,),{})(SECRET)
    service.release.set();coordinator.start(ID);finished(workers)
    snapshot=coordinator.snapshot()
    assert snapshot.unexpected_error.category=='InternalError'
    assert 'SENTINEL_SECRET_CLASS' not in repr(snapshot)
    assert_sanitized(snapshot,caplog)


def test_invalid_service_result_finishes_safely_and_releases_slot(fixture,caplog):
    coordinator,service,workers,_=fixture
    original=service.acquire
    service.acquire=lambda *args,**kwargs:None
    coordinator.start(ID);finished(workers)
    snapshot=coordinator.snapshot()
    assert snapshot.status is Status.FINISHED and snapshot.result is None and snapshot.unexpected_error is not None
    assert_sanitized(snapshot,caplog)
    service.acquire=original;service.release.set()
    assert coordinator.start(OTHER).outcome is Start.STARTED
    finished(workers)
    assert coordinator.snapshot().result.outcome is Outcome.SUCCEEDED


def test_start_time_factory_bound_once_and_busy_does_not_construct(fixture):
    coordinator,service,workers,_=fixture
    other=ControlledService();other.release.set()
    calls=[]
    assert coordinator.start(ID,service_factory=lambda:(calls.append('A'),service)[1]).outcome is Start.STARTED
    assert service.entered.wait(5)
    assert coordinator.start(OTHER,service_factory=lambda:(calls.append('B'),other)[1]).outcome is Start.ALREADY_RUNNING
    assert calls==['A'] and other.calls==[] and coordinator.snapshot().paper_id==ID
    service.release.set();finished(workers)
    assert coordinator.start(OTHER,service_factory=lambda:(calls.append('B'),other)[1]).outcome is Start.STARTED
    finished(workers)
    assert calls==['A','B'] and [paper for paper,_ in service.calls]==[ID]
    assert [paper for paper,_ in other.calls]==[OTHER]


def test_factory_failure_sanitized_and_next_start_possible(fixture,caplog):
    coordinator,service,workers,_=fixture
    def broken():raise RuntimeError(SECRET)
    assert coordinator.start(ID,service_factory=broken).outcome is Start.START_FAILED
    assert coordinator.snapshot().status is Status.FINISHED and service.calls==[] and workers==[]
    assert_sanitized(coordinator.snapshot(),caplog)
    service.release.set()
    assert coordinator.start(OTHER,service_factory=lambda:service).outcome is Start.STARTED
    finished(workers)
