# Superseded v0.6.3 import tests

These four modules preserve the A3–A5 pre-simplification regression suites
unchanged. Their assertions require durable `export_attempt` writes,
permanent import exclusion after `pending`/`uncertain` and repeated
whole-Workspace scans. Those behaviors are superseded by the current
`SPEC.md` §42.3–42.5 and cannot simultaneously pass with the new contract.

The preserved files are deliberately named without the `test_` prefix to
exclude them from current pytest discovery. Active tests remain at the
original paths and in `tests/test_import_simplification.py`; they exercise
transient import authority, retained legacy metadata, safe parent completion,
retry after interruption, cross-process exclusion and UI/Connector behavior.

This archive is historical source material, not a second runnable product
contract. It must not be used as evidence that the old permanent blocking
behavior remains supported.
