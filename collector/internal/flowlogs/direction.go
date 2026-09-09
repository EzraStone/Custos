package flowlogs

import "github.com/EzraStone/Custos/collector/internal/wire"

// Direction inference, for accounts whose flow log format has no
// flow-direction field.
//
// It is not decoration. Every finding Custos makes rests on the asymmetry
// between what a workload sends and what it receives, and a record whose
// direction is unknown contributes to neither side. The 2014 default format
// has no such field and most existing log archives are in it, so without this
// "read the logs you already have" would mean "read them and conclude
// nothing".
//
// Two sources, in order of authority:
//
//  1. The interface's own address, as AWS reports it. Authoritative.
//  2. The address common to every record on that interface. Exact rather than
//     heuristic: one end of every record on an interface is that interface,
//     so an address present in all of them and unique is that interface's.
//
// What is left undecided stays undecided. A guessed direction is worse than a
// dropped record: it lands in the wrong half of the ratio the entire
// classifier is built on, and it lands there silently.

// DirectionStats reports what inference achieved, for the coverage note.
type DirectionStats struct {
	Read      int // the format carried it
	Inferred  int
	Undecided int
}

// Decided is the fraction of records whose direction is known.
func (d DirectionStats) Decided() float64 {
	total := d.Read + d.Inferred + d.Undecided
	if total == 0 {
		return 1
	}
	return float64(d.Read+d.Inferred) / float64(total)
}

// InferDirection fills in the direction of records that have none, and returns
// the records whose direction is known. Records left undecided are dropped:
// the wire contract requires a direction, and shipping a guessed one would put
// bytes on the wrong side of the ratio every finding rests on.
func InferDirection(
	records []wire.FlowRecord, addressByInterface map[string]string,
) ([]wire.FlowRecord, DirectionStats) {
	var stats DirectionStats

	local := make(map[string]string, len(addressByInterface))
	for eni, addr := range addressByInterface {
		if addr != "" {
			local[eni] = addr
		}
	}
	for eni, addr := range commonAddresses(records) {
		if _, known := local[eni]; !known {
			local[eni] = addr
		}
	}

	out := records[:0:0]
	for _, r := range records {
		if r.Direction == wire.Egress || r.Direction == wire.Ingress {
			stats.Read++
			out = append(out, r)
			continue
		}

		addr, ok := local[r.InterfaceID]
		switch {
		case !ok:
			stats.Undecided++
			continue
		case r.SrcAddr == addr:
			r.Direction = wire.Egress
		case r.DstAddr == addr:
			r.Direction = wire.Ingress
		default:
			// The interface has this address and neither end is it — a
			// secondary private IP, or traffic the interface only forwarded.
			stats.Undecided++
			continue
		}
		stats.Inferred++
		out = append(out, r)
	}
	return out, stats
}

// commonAddresses returns, per interface, the one address present at some end
// of every record on it — which is that interface's own address.
//
// Exact where it answers at all. It cannot answer for an interface that talked
// to exactly one peer across the whole window, because then both addresses are
// in every record and there is nothing to separate them; those interfaces need
// the attributed address, and stay undecided without it.
func commonAddresses(records []wire.FlowRecord) map[string]string {
	seen := make(map[string]map[string]int)
	total := make(map[string]int)

	for _, r := range records {
		if r.Direction == wire.Egress || r.Direction == wire.Ingress {
			continue
		}
		total[r.InterfaceID]++
		counts, ok := seen[r.InterfaceID]
		if !ok {
			counts = make(map[string]int, 2)
			seen[r.InterfaceID] = counts
		}
		counts[r.SrcAddr]++
		if r.DstAddr != r.SrcAddr {
			counts[r.DstAddr]++
		}
	}

	out := make(map[string]string, len(seen))
	for eni, counts := range seen {
		var found string
		for addr, n := range counts {
			if n != total[eni] {
				continue
			}
			if found != "" {
				found = "" // two candidates: one peer only, nothing to choose
				break
			}
			found = addr
		}
		if found != "" {
			out[eni] = found
		}
	}
	return out
}
