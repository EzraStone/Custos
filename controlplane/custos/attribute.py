"""Principal to owner.

This is the least glamorous module in the repository and it deserves
disproportionate investment. A finding with no owner is noise, and noise gets
the tool uninstalled — the report goes to a security lead who forwards it to
nobody, because there is nobody to forward it to.

Resolution walks four sources in descending order of reliability. Each returns
a confidence, because "we are certain the payments team owns this" and "the
role name starts with pay-" are different claims and reporting them
identically is how a report loses credibility on the one finding that gets
checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Method(StrEnum):
    RESOURCE_TAG = "resource_tag"
    """A tag on the compute resource. The strongest source and the one the
    customer already maintains for cost allocation."""

    ROLE_TAG = "role_tag"
    """A tag on the IAM role itself."""

    PATH_CONVENTION = "path_convention"
    """The IAM path, e.g. /payments/service-role/. Real convention at
    organisations with any IAM discipline."""

    NAME_HEURISTIC = "name_heuristic"
    """Inferred from the role name. Weak, reported as weak, and never presented
    as a fact."""

    NONE = "none"


OWNER_TAG_KEYS = ("owner", "Owner", "team", "Team", "owning_team", "OwningTeam")
CONTACT_TAG_KEYS = ("contact", "Contact", "owner_email", "OwnerEmail", "slack", "Slack")

_METHOD_CONFIDENCE = {
    Method.RESOURCE_TAG: 0.95,
    Method.ROLE_TAG: 0.90,
    Method.PATH_CONVENTION: 0.65,
    Method.NAME_HEURISTIC: 0.35,
    Method.NONE: 0.0,
}


@dataclass(frozen=True, slots=True)
class PrincipalFacts:
    """What the collector could read about a principal, read-only.

    Everything here comes from IAM and resource describe calls that a read-only
    cross-account role permits (SEC-16).
    """

    principal: str
    role_tags: dict[str, str] = field(default_factory=dict)
    resource_tags: dict[str, str] = field(default_factory=dict)
    iam_path: str = "/"
    compute: str = ""
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class Attribution:
    team: str
    contact: str
    method: Method
    confidence: float

    @property
    def resolved(self) -> bool:
        return bool(self.team or self.contact)


UNRESOLVED = Attribution(team="", contact="", method=Method.NONE, confidence=0.0)

_ROLE_NAME = re.compile(r"role/(?:.*/)?(?P<name>[^/]+)$")
_SUFFIXES = (
    "-task", "-role", "-runner", "-service", "-svc", "-lambda", "-fn",
    "-execution", "-exec", "-worker", "-agent", "-job",
)


def _first_tag(tags: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = tags.get(key, "").strip()
        if value:
            return value
    return ""


def role_name(principal: str) -> str:
    match = _ROLE_NAME.search(principal)
    return match.group("name") if match else principal


def _name_stem(principal: str) -> tuple[str, bool]:
    """Strip conventional suffixes to leave something team-shaped.

    Returns the stem and whether a suffix was actually recognised. That flag
    matters: stripping `-worker` off `checkout-worker` means we learned the
    name follows a convention, whereas `svc0001` tells us nothing at all and
    must not be reported as a team.
    """
    name = role_name(principal)
    for suffix in _SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)].strip("-_"), True
    return name.strip("-_"), False


def resolve(facts: PrincipalFacts) -> Attribution:
    """Resolve an owner, or return UNRESOLVED.

    Returning UNRESOLVED is a correct and common outcome. SEC-20 requires those
    findings to be segregated in the report rather than dropped or padded with
    a guess.
    """
    for tags, method in (
        (facts.resource_tags, Method.RESOURCE_TAG),
        (facts.role_tags, Method.ROLE_TAG),
    ):
        team = _first_tag(tags, OWNER_TAG_KEYS)
        contact = _first_tag(tags, CONTACT_TAG_KEYS)
        if team or contact:
            return Attribution(team, contact, method, _METHOD_CONFIDENCE[method])

    path = facts.iam_path.strip("/")
    if path:
        # /payments/service-role/ -> payments
        segment = path.split("/")[0]
        if segment and segment not in ("service-role", "aws-service-role"):
            return Attribution(
                segment, "", Method.PATH_CONVENTION, _METHOD_CONFIDENCE[Method.PATH_CONVENTION]
            )

    stem, recognised = _name_stem(facts.principal)
    # A guess is only worth making when the name follows some convention: a
    # recognised suffix was stripped, or the name is hyphenated. An opaque
    # identifier like svc0001 yields nothing, and inventing an owner for it
    # would be worse than reporting none.
    if stem and (recognised or "-" in stem):
        return Attribution(
            stem.split("-")[0], "", Method.NAME_HEURISTIC,
            _METHOD_CONFIDENCE[Method.NAME_HEURISTIC],
        )

    return UNRESOLVED
