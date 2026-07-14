"""Scans generated file content for obviously dangerous patterns before it
ever reaches disk — hardcoded-looking secrets and code that shells out /
self-executes. Heuristic and deliberately narrow: it only flags shapes that
are very unlikely to appear in legitimate frontend code, to keep false
positives low (placeholders like "your_api_key"/"xxxxxxxx" are excluded).
Runs as a normal verifier, so a hit is fed back into codegen's retry prompt
exactly like a failed tsc/eslint/build check — the harness doesn't merely
warn after the fact, it can ask the model to fix it.
"""

from __future__ import annotations

import re
from typing import Any

from frontforge.core.session import RunSession
from frontforge.shared.types import VerificationIssue

_PLACEHOLDER_VALUES = {
    "xxxxxxxx",
    "your_api_key",
    "your-api-key",
    "changeme",
    "change_me",
    "replace_me",
    "replaceme",
    "todo",
    "example",
    "",
}

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "looks like an API secret key (sk-...)"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "looks like an AWS access key ID"),
    (
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        "an embedded private key",
    ),
]

_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*['\"]([A-Za-z0-9+/_\-]{6,})['\"]"
)

_DANGEROUS_CODE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"child_process"), "spawns a child process from generated frontend code"),
    (re.compile(r"\beval\s*\("), "uses eval()"),
    (re.compile(r"curl[^\n]{0,80}\|\s*(sh|bash)\b"), "pipes a remote download straight into a shell"),
    (re.compile(r"rm\s+-rf\s+/(?!\S)"), "destructive filesystem command (rm -rf /)"),
]


def _looks_like_real_secret(value: str) -> bool:
    stripped = value.strip().lower()
    if stripped in _PLACEHOLDER_VALUES:
        return False
    if len(set(stripped)) <= 2:  # "xxxxxxxx", "00000000", ...
        return False
    return True


class SecurityScanVerifier:
    name = "security_scan"

    async def verify(
        self, *, stage_id: str, output: dict[str, Any], session: RunSession
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []

        for file in output.get("files", []):
            path = file.get("path", "?")
            content = file.get("content", "") or ""

            for pattern, description in _SECRET_PATTERNS:
                if pattern.search(content):
                    issues.append(
                        VerificationIssue(verifier=self.name, message=f"{path}: {description}")
                    )

            for match in _CREDENTIAL_ASSIGNMENT.finditer(content):
                if _looks_like_real_secret(match.group(2)):
                    issues.append(
                        VerificationIssue(
                            verifier=self.name,
                            message=f"{path}: hardcoded credential-looking value ({match.group(1)})",
                        )
                    )

            for pattern, description in _DANGEROUS_CODE_PATTERNS:
                if pattern.search(content):
                    issues.append(
                        VerificationIssue(verifier=self.name, message=f"{path}: {description}")
                    )

        return issues
