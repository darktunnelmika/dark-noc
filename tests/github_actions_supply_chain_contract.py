#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REMOTE_USE = re.compile(r"^\s*-\s+uses:\s+([^\s#]+)")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

errors: list[str] = []
workflow_files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))

for path in workflow_files:
    if path.name.startswith("prepare-"):
        errors.append(f"stale preparation workflow must be removed: {path.relative_to(ROOT)}")

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = REMOTE_USE.match(line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith("./"):
            continue
        if "@" not in target:
            errors.append(f"{path.relative_to(ROOT)}:{lineno}: action reference has no immutable ref: {target}")
            continue
        action, ref = target.rsplit("@", 1)
        if not action or not FULL_SHA.fullmatch(ref):
            errors.append(
                f"{path.relative_to(ROOT)}:{lineno}: remote action must be pinned to a full 40-char commit SHA: {target}"
            )

if errors:
    raise AssertionError("GitHub Actions supply-chain contract failed:\n- " + "\n- ".join(errors))

print(f"GitHub Actions supply-chain contract passed for {len(workflow_files)} workflow(s).")
