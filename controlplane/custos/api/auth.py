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
    """Who is calling.

    A set of AWS accounts, no roles. Plural because one customer in the target
    profile — 80 to 400 engineers — routinely runs five to fifty AWS accounts,
    and issuing a token per account would mean fifty collectors, fifty secrets
    to rotate, and fifty registers that cannot be read together.

    This is not multi-tenancy. Every account here belongs to one customer, and
    a token still cannot reach an account it was not issued for. The isolation
    boundary moved from one account to a named set; it did not disappear.
    """

    accounts: frozenset[str]
    label: str = ""

    @property
    def account_id(self) -> str:
        """The single account, for the common case.

        Raises when a token covers several, because code that assumes one
        account should fail loudly rather than silently pick the first — which
        would attribute one account's findings to another.
        """
        if len(self.accounts) != 1:
            raise ValueError(
                f"this credential covers {len(self.accounts)} accounts; "
                "the caller must say which one it means"
            )
        return next(iter(self.accounts))

    def covers(self, account_id: str) -> bool:
        return account_id in self.accounts


class TokenStore:
    """Maps bearer tokens to accounts.

    Loaded from the environment as `CUSTOS_TOKENS=account:token,account:token`.
    A token appearing against several accounts covers all of them, which is how
    one customer's fleet is expressed:

        CUSTOS_TOKENS=111111111111:tok-acme,222222222222:tok-acme

    A file or a secrets manager replaces this without touching a caller; what
    must not change is that a token names a fixed set of accounts and nothing
    else.
    """

    def __init__(self, tokens: dict[str, str | set[str] | frozenset[str]] | None = None) -> None:
        # token -> the accounts it covers
        self._tokens: dict[str, frozenset[str]] = {}
        for token, accounts in (tokens or {}).items():
            if isinstance(accounts, str):
                accounts = {accounts}
            self._tokens[token] = frozenset(accounts)

    @classmethod
    def from_env(cls, getenv=os.getenv) -> TokenStore:
        raw = (getenv("CUSTOS_TOKENS") or "").strip()
        tokens: dict[str, set[str]] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            account, _, token = pair.partition(":")
            account, token = account.strip(), token.strip()
            if account and token:
                tokens.setdefault(token, set()).add(account)
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
        for candidate, accounts in self._tokens.items():
            if hmac.compare_digest(candidate, token):
                return Principal(accounts=accounts)
        return None


def parse_bearer(header: str | None) -> str:
    """Extract a bearer token from an Authorization header."""
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()
