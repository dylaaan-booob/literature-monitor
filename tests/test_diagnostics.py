from literature_monitor.diagnostics import (
    RunDiagnostic,
    RunDiagnosticKind as Kind,
    RunDiagnosticSummary,
    summarize_run_diagnostics,
)


def test_summary_counts_logical_objects_in_declaration_order_without_mutating_input():
    diagnostics = (
        RunDiagnostic(Kind.REPEATED_TITLE_SEPARATION, "Separate works", ("W1", "W2", "W3")),
        RunDiagnostic(Kind.NON_CANDIDATE_EXCLUSION, "Excluded", ("W4", "10.5555/a", "W5")),
        RunDiagnostic(Kind.SCOPE_DISPUTE, "Scope unresolved", ("W6", "10.5555/b"), "Biometrics"),
        RunDiagnostic(Kind.NON_CANDIDATE_EXCLUSION, "Excluded", ("W7",)),
    )
    before = tuple((item.kind, item.message, item.record_ids, item.journal) for item in diagnostics)
    expected = (
        RunDiagnosticSummary(Kind.NON_CANDIDATE_EXCLUSION, 2),
        RunDiagnosticSummary(Kind.SCOPE_DISPUTE, 1),
        RunDiagnosticSummary(Kind.REPEATED_TITLE_SEPARATION, 1),
    )
    assert summarize_run_diagnostics(diagnostics) == expected
    assert summarize_run_diagnostics(tuple(reversed(diagnostics))) == expected
    assert tuple((item.kind, item.message, item.record_ids, item.journal) for item in diagnostics) == before


def test_empty_diagnostics_have_no_summary():
    assert summarize_run_diagnostics(()) == ()
