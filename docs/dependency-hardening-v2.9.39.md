# DARK NOC Dependency Hardening — v2.9.39

This release advances exactly one dependency: `cryptography` from 44.0.0 to 50.0.1.

Validation scope:
- exact production pin
- Hub import compatibility
- Fernet key generation, encryption and decryption
- tampered-token rejection through `InvalidToken`
- full Hub + Agent regression suite
- reproducible release packaging

No other dependency is intentionally changed in this release.
