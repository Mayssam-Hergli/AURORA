# Hardening baseline

Base commit: `2de8c0517de5cf8e0d40d57fc8650d525da96a8f` (`p9-integration`). New branch: `rami`.

Environment: Ubuntu Linux, isolated CPython 3.11.15 virtual environment; dependencies synchronized from the existing requirements.lock with CPU PyTorch 2.14.1. No board or broker was present for the baseline.

Before source modifications: Python suite **176 passed, 5 skipped**, no failures; all **8 dashboard tests passed**. Five skipped tests require a broker. Captured baseline stdout is preserved in the task's work directory.

Historical manifest (dirty source revision 596a08a, 12 October): synthetic ID ensemble recall 1.00; median warning lead 133 s (IQR 114–162 s); realized FPR 0.15%; pre-onset alarms 0.263/satellite-hour. Closed loop: 24 episodes, 18 failing without defence, 13 prevented, no new failures. These are historical values, not valid results for hardened inputs/models.

Confirmed inspection issues: backward fill in offline features; missing freshness ML inputs; thermal/EPS-only planner rollout checks and omitted observed ADCS state; generic isolation actually cuts heater; missing uncertainty gate is inactive; source mismatch can overwrite state; constant uncertainty objective; fixed-threshold metrics called action metrics; stale root README and report; paho missing from project dependencies. The attached paper's 2.3 s alert differs from the demo note's ~6 s, without raw run logs to reconcile it.
