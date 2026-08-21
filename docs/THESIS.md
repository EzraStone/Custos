# The product thesis

## The shape we are copying

A license plate reader network works because it does three things in sequence,
and each one is worthless without the others:

1. **It sees.** Cameras cover roads nobody thought to watch. Coverage is not
   opt-in — you do not have to register your car for the camera to read it.
2. **It resolves identity.** A plate becomes an owner, a registration, a
   history. A sighting becomes something a person can act on.
3. **It can interdict.** When a plate matches a list, something happens in real
   time, not in a monthly report.

Custos is that pattern for autonomous agents.

| Camera network | Custos | Built |
| --- | --- | --- |
| Cameras on roads nobody watches | Collector reading flow logs and CloudTrail — sees agents nobody enrolled | Yes |
| Plate → owner → history | Attributor: principal → tags → team → human, across EC2, Lambda, ECS | Yes |
| A sighting becomes a record | Register, baselines, and what changed since last week | Yes |
| Hotlist hit → interdiction | Checkpoint: policy match → allow, deny, or hold for human | Not started |

**See what agents are doing, know where they came from, and stop them when they
should be stopped.** That is the whole company in one sentence, and every
architectural decision in this repository should be traceable to one of those
three verbs.

## Why this ordering is forced

The three capabilities have a strict dependency order, and it runs one way.

Enforcement without discovery has an empty register. A checkpoint can only
govern agents that were enrolled, and the agents that were enrolled are exactly
the ones that were already known, reviewed, and least likely to be the problem.

Discovery without enforcement is a report read once and filed. Awareness alone
does not renew a contract.

So: build the seeing, sell the stopping. The Register populates the tables the
Checkpoint governs.

**Where that leaves us.** The seeing works end to end and the attribution
resolves to a named team. The stopping is deliberately not started, because
§12 says not before a paying customer asks for it and starting it early would
be the most expensive way to avoid the conversation that actually decides
whether this works.

## Where the analogy must not be used out loud

The comparison is structurally correct and it is the right internal north star.
It is the wrong external pitch, for a specific and predictable reason.

Pervasive-sensor companies carry a civil-liberties argument that arrives before
the product does. If a security reviewer or a partner hears "surveillance
network," the next question is about the people being watched — and we then
spend the meeting defending a position we did not need to take.

The defensible statement of the same idea, which happens to also be true:

> Custos inventories **software**, not people. Every record describes a machine
> principal, an endpoint, and a byte count. Prompts, completions, and payload
> bodies never leave the customer account under any configuration (SEC-18), and
> the collector cannot be pointed at an environment its operator lacks
> legitimate access to.

Those two constraints are what make the ethical position hold, which is why they
are invariants in `docs/SECURITY-INVARIANTS.md` and enforced structurally in the
collector rather than by policy. Do not trade them away for a feature.

Three places that position has already been tested in code, each recorded where
it was decided:

- Load balancer access logs carry request URLs, query strings, user agents, and
  client addresses. The collector takes four fields — timestamp, target, and two
  byte counts — and the rest cannot survive parsing.
- Structured logs follow the same rule as the wire, and a third-party HTTP
  client logging full URLs was caught and quieted rather than tolerated.
- Drift findings are phrased as questions to the workload's owner and state
  outright that they are not evidence of a problem, because the data cannot
  carry a stronger claim.

None of those were forced by an outside requirement. They are the position
being kept when it would have been easier not to, which is the only test of
whether it is real.

## The sentence the company is built to be able to say

> We scanned N companies. Every one had AI agents their security team had never
> registered — M of them holding write credentials to production.

Everything in the A0 phase exists to find out whether that sentence will be
true.
