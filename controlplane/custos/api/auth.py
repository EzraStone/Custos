"""Collector authentication.

Deliberately minimal. The specification's "not building" list includes
multi-tenancy, RBAC, and SSO before a paying customer, and this is not a way
around that — it is the smallest thing that lets a collector prove which account
it is shipping for, which the API cannot function without.

What it is: a per-account bearer token, compared in constant time, mapping to
exactly one account ID. What it is not: user identity, roles, sessions, or any
notion of a person. Operator actions that need a human identity — granting
imprimatur is the only one — take that identity explicitly rather than
inferring it from a token, because a token is a machine and SEC-17 requires a
person.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling. One account, no roles."""

    account_id: str
    label: str = ""


class TokenStore:
    """Maps bearer tokens to accounts.

    Loaded from the environment as `CUSTOS_TOKENS=account:token,account:token`.
    A file or a secrets manager replaces this without touching a caller; what
    must not change is that a token identifies an account and nothing else.
    """

    def __init__(self, tokens: dict[str, str] | None = None) -> None:
        # token -> account_id
        self._tokens = dict(tokens or {})

    @classmethod
    def from_env(cls, getenv=os.getenv) -> TokenStore:
        raw = (getenv("CUSTOS_TOKENS") or "").strip()
        tokens: dict[str, str] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            account, _, token = pair.partition(":")
            if account and token:
                tokens[token] = account
        return cls(tokens)

    def __len__(self) -> int:
        return len(self._tokens)

    def resolve(self, token: str) -> Principal | None:
        """Resolve a bearer token, in constant time with respect to the secret.

        The comparison is constant-time per candidate. The number of candidates
        leaks how many accounts exist, which is not a secret worth protecting
        and is not worth the complexity of a keyed lookup that would leak the
        same thing through timing anyway.
        """
        if not token:
            return None
        for candidate, account in self._tokens.items():
            if hmac.compare_digest(candidate, token):
                return Principal(account_id=account)
        return None


def parse_bearer(header: str | None) -> str:
    """Extract a bearer token from an Authorization header."""
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()
