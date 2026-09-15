# DARK NOC Dependency Hardening — v2.9.38

This release starts the one-dependency-at-a-time hardening wave.

## Changed
- `python-multipart`: `0.0.20` → `0.0.32`

## Validation boundary
- Hub requirements remain exact pins.
- Existing multipart upload authentication and body-size protection are unchanged.
- Existing SSH upload/file regression tests remain authoritative for behavior.
- Full Hub/Agent regression suite and reproducible release build are required before merge.

No other dependency is changed in this release.
