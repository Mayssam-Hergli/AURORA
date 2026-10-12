"""CASSANDRA core: ingest -> forecast -> monitor -> sandbox -> act -> explain (docs/MASTER_PLAN.md Phase 4).

Ground-truth isolation: nothing in this package subscribes to, imports or reads aurora/v1/gt/#,
aurora/v1/redteam/#, labels.json, ml_labels.json or any _malicious / _auth_ok flag
(tests/cassandra/test_gt_isolation.py enforces it).
"""
