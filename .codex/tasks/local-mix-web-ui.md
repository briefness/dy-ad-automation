Goal
- Add a localhost-only Chinese web workbench for the existing local-asset remix pipeline.
- Keep the CLI and all local-material evidence, timeline, quality, and output contracts unchanged.

Non-goals
- No AI-generation mode, manual timeline editor, uploads, authentication, database, mobile-specific UI, or API-key editor.

Acceptance criteria
- `python local_web.py` binds only to 127.0.0.1 and serves a fresh desktop-first workbench.
- Asset paths are validated beneath configurable `LOCAL_ASSET_ROOT` before preflight or execution.
- Environment checks, explicit async preflight, editable recommendations, one active worker, polling, logs, hard cancellation, result preview, reports, and recent runs work.
- Shutdown terminates the active worker; no new external calls occur after cancellation.
- Existing `OUTPUT_DIR`, stable naming, resume behavior, pipeline functions, and CLI behavior remain authoritative.
- Targeted path, preflight, job lifecycle, cancellation, artifact-serving, and HTTP smoke tests pass without real model or FFmpeg calls.

Baseline
- Branch: main
- Commit: d0c31239d607a2cb44e5f31a02d558270c17215d

Decisions
- Python standard-library HTTP server plus native HTML/CSS/JavaScript; no new dependencies.
- Process-isolated workers and a single active task.
- Polling for status; existing pipeline output remains the source for logs and artifacts.
- Chinese-only, desktop-first, narrow-screen usable, fresh light visual style.

Changed files
- `local_web.py` and `local_web_app/`: localhost-only stdlib server, Chinese workbench UI, validation, environment checks, cached preflight, process worker/cancellation, polling, artifact/history routes.
- `tests/test_local_web.py`: path, list normalization, artifact protection, and HTTP smoke coverage.

Validation
- `rtk python -m py_compile local_web.py local_web_app/*.py`
- `rtk pytest -q tests/test_local_web.py` -> 7 passed.
- Second pass: matching completed preflight required before formal run; worker process-group cancellation and preflight log capture; API-key-aware environment gate; artifact suffix/path hardening; occupied-port fallback; refreshed light UI.

Remaining work
- Parent agent to perform final diff review and broader integration verification.
