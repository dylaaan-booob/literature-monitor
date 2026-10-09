"""One-shot Connector completion adapter; legacy per-Paper saving is retired."""

from dataclasses import dataclass

from literature_monitor.application.export_attempts import ExportCompletionResult, resolve_export_completion
from literature_monitor.web.capture_coordinator import CaptureCompletion, CaptureCoordinator


@dataclass(frozen=True)
class CompletionProcessResult:
    completion: CaptureCompletion | None
    result: ExportCompletionResult | None


def process_capture_completion(coordinator: CaptureCoordinator) -> CompletionProcessResult:
    completion = coordinator.consume_completion()
    if completion is None:
        return CompletionProcessResult(None, None)
    return CompletionProcessResult(completion, resolve_export_completion(completion, coordinator=coordinator))
