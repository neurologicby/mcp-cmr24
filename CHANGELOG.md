# Changelog

## 0.1.0 - 2026-09-14

- Require authenticated proxy proof before accepting caller and scope headers.
- Add bounded per-client request limiting and strict request metadata validation.
- Enable MCP-compatible CORS for explicitly allowed browser origins.
- Validate cargo, mutation and reference API responses before returning or caching them.
- Replace unbounded keyed cache locks with self-cleaning single-flight tasks.
- Add reproducible Windows dependencies alongside the Linux container lock.
- Add cross-platform tests, static analysis, dependency audit and container build CI.
- Document the supported single-process deletion-confirmation model and production checklist.

## 0.0.1 - 2026-09-14

- Initial public release.
