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

| Camera network | Custos |
| --- | --- |
| Cameras on roads nobody watches | Collector reading flow logs and CloudTrail — sees agents nobody enrolled |
| Plate → owner → history | Attributor: principal → resource tags → team → human |
| Hotlist hit → interdiction | Checkpoint: policy match → allow, deny, or hold for human |

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

## The sentence the company is built to be able to say

> We scanned N companies. Every one had AI agents their security team had never
> registered — M of them holding write credentials to production.

Everything in the A0 phase exists to find out whether that sentence will be
true.
