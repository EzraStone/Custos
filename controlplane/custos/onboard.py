"""Getting a customer from "yes" to a first scan.

The entry motion is a free read-only scan that has to clear a platform lead's
bar without a meeting. Everything between "yes" and a role ARN is friction, and
friction at that moment is where a soft lead goes quiet.

So this generates the exact artifacts a customer needs — their external ID,
their token, their terraform.tfvars, and the paragraph to paste into a ticket —
rather than leaving someone to assemble them by hand and get one wrong.

The external ID and the token are generated here and never stored in this
module. They are printed once, and re-running produces different ones. A
credential that can be recovered from a repository is a credential in every
fork of it.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

TOKEN_BYTES = 24
"""192 bits. Long enough that guessing is not a threat model, short enough to
paste into a terminal without wrapping."""

EXTERNAL_ID_BYTES = 16
"""AWS requires at least 2 characters and allows 1224. This is comfortably
above anything guessable and comfortably below anything awkward."""

_ACCOUNT = re.compile(r"^\d{12}$")


class InvalidAccount(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Onboarding:
    account_id: str
    external_id: str
    token: str
    endpoint: str
    custos_account_id: str

    @property
    def tfvars(self) -> str:
        """terraform.tfvars for the customer to apply.

        `log_buckets` is commented rather than omitted. It is empty for an
        account on CloudWatch delivery with no access logs, and required for
        every other shape — and a variable nobody knew existed is how a
        customer ends up applying a role that cannot read their own logs.
        """
        return (
            f'external_id       = "{self.external_id}"\n'
            f'custos_account_id = "{self.custos_account_id}"\n'
            "# log_buckets     = []  "
            "# name any bucket holding flow logs or access logs\n"
        )

    @property
    def collector_env(self) -> str:
        """Environment for the collector, once the role exists."""
        return "\n".join([
            f"CUSTOS_ENDPOINT={self.endpoint}",
            f"CUSTOS_TOKEN={self.token}",
            f"CUSTOS_ACCOUNT_ID={self.account_id}",
            f"CUSTOS_EXTERNAL_ID={self.external_id}",
            "CUSTOS_ROLE_ARN=arn:aws:iam::"
            f"{self.account_id}:role/custos-discovery",
            "AWS_REGION=us-east-1",
            "# Every region you run in, comma separated. AWS_REGION is always",
            "# included; --check names the ones that have flow logs:",
            "# CUSTOS_REGIONS=eu-west-1,ap-south-1",
            "CUSTOS_FLOW_LOGS=/aws/vpc/flowlogs",
            "# Or point at flow logs you already keep, in whatever format they",
            "# are. S3 needs nothing else; CloudWatch needs the format string:",
            "# CUSTOS_FLOW_LOG_FORMAT=${version} ${account-id} ${interface-id} ...",
            "# CUSTOS_ACCESS_LOGS=s3://your-alb-logs/AWSLogs/...  "
            "# optional, lifts recall from 60% to 100%",
        ])

    @property
    def tokens_env(self) -> str:
        """The value to add to the control plane's CUSTOS_TOKENS."""
        return f"{self.account_id}:{self.token}"

    @property
    def message(self) -> str:
        """What to send the customer.

        Written to be pasted into a ticket unchanged. It leads with what the
        thing does not do, because that is the first question and answering it
        before it is asked is what makes thirty minutes possible.
        """
        return f"""\
Custos read-only discovery — setup for account {self.account_id}

This creates one IAM role with read-only permissions. It creates no compute,
installs nothing on any host, and has an explicit deny on every mutating
action. Removing it is `terraform destroy` and leaves nothing behind.

1. Look at what it does before granting anything:

     git clone https://github.com/EzraStone/Custos
     cd Custos/collector && go build ./cmd/custos-collector
     ./custos-collector --explain

2. Apply the Terraform:

     cd Custos/collector/deploy/terraform
     cat > terraform.tfvars <<'EOF'
{self.tfvars}\
     EOF
     terraform init && terraform apply

3. Send back the `role_arn` output. That is the only thing we need.

If you already have VPC Flow Logs, use those. The Terraform above creates one
in our format, and you do not have to take it — flow logs bill per gigabyte
ingested and a second copy of a busy account's traffic is a real line item, on
top of a second change request. Point us at the log group or S3 prefix you
already have instead.

For S3 there is nothing else to send: AWS writes the field names at the top of
every object and we read them. For CloudWatch, send the format string you gave
AWS. Either way, if any of your logs are in S3 — flow logs delivered there, or
the access logs below — name those buckets in `log_buckets` above, or the role
will not be able to read them. Six fields have to be in it — interface-id, srcaddr, dstaddr, bytes,
start, end — and `--check` tells you exactly what any others being absent
costs, before you commit to anything.

Optional and worth it: if you can also point us at your load balancer access
logs, our recall goes from 60% to 100% — and the agents we miss without them
are the low-volume ones, which are usually the ones you would most want to know
about. We take four fields from those logs (timestamp, target, and two byte
counts); the URL, query string, user agent, and client address are discarded at
parse time and cannot reach us.

One more thing, and it is the one most likely to make a first report look
reassuring for the wrong reason: an AWS account is a region-by-region thing. A
workload in eu-west-1 has its own flow logs, its own network interfaces, and no
representation at all in a scan of us-east-1 — so "no unsanctioned agents" from
one region is a statement about that region, and our report says so rather than
implying otherwise.

`./custos-collector --check` names which of your regions have flow logs.
Whichever they are, list them and we cover them in one run.

One thing worth checking on your side before the first scan: whether the
network interfaces behind your internal services carry a `Name` tag. We name
what an agent reaches from those tags and from the descriptions AWS writes for
its own managed services, and anything neither covers appears as an IP address.
That does not change what we find — it changes whether the person approving a
finding is reading "billing-api" or "10.0.4.23". `./custos-collector --check`
reports how many we could name before you commit to anything.
"""


def generate(
    account_id: str,
    endpoint: str,
    custos_account_id: str = "000000000000",
) -> Onboarding:
    """Generate one customer's onboarding material.

    The account ID is validated because a typo here produces a role nobody can
    assume and a confusing hour for someone who was doing us a favour.
    """
    account_id = account_id.strip()
    if not _ACCOUNT.match(account_id):
        raise InvalidAccount(
            f"{account_id!r} is not a 12-digit AWS account ID"
        )
    if not endpoint.startswith("https://"):
        raise ValueError("the control plane endpoint must be https")

    return Onboarding(
        account_id=account_id,
        external_id=secrets.token_urlsafe(EXTERNAL_ID_BYTES),
        token=secrets.token_urlsafe(TOKEN_BYTES),
        endpoint=endpoint.rstrip("/"),
        custos_account_id=custos_account_id,
    )
