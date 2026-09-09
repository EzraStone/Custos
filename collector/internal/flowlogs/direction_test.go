package flowlogs

import (
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

func rec(eni, src, dst string, direction wire.Direction) wire.FlowRecord {
	return wire.FlowRecord{
		InterfaceID: eni, SrcAddr: src, DstAddr: dst, Bytes: 1000,
		Direction: direction,
		Start:     time.Unix(1754827200, 0).UTC(),
		End:       time.Unix(1754827259, 0).UTC(),
	}
}

func TestADirectionTheFormatCarriedIsNotSecondGuessed(t *testing.T) {
	in := []wire.FlowRecord{rec("eni-1", "10.0.1.5", "1.2.3.4", wire.Ingress)}
	out, stats := InferDirection(in, map[string]string{"eni-1": "10.0.1.5"})

	if out[0].Direction != wire.Ingress {
		t.Fatalf("a stated direction was overwritten: %v", out[0].Direction)
	}
	if stats.Read != 1 || stats.Inferred != 0 {
		t.Fatalf("stats %+v", stats)
	}
}

func TestTheInterfaceAddressDecidesDirection(t *testing.T) {
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.1.5", "160.79.104.10", ""),
		rec("eni-1", "160.79.104.10", "10.0.1.5", ""),
	}
	out, stats := InferDirection(in, map[string]string{"eni-1": "10.0.1.5"})

	if len(out) != 2 || out[0].Direction != wire.Egress || out[1].Direction != wire.Ingress {
		t.Fatalf("directions: %v %v", out[0].Direction, out[1].Direction)
	}
	if stats.Inferred != 2 || stats.Undecided != 0 {
		t.Fatalf("stats %+v", stats)
	}
}

func TestTheAddressCommonToEveryRecordIsTheInterfaceItself(t *testing.T) {
	// No attribution at all. One end of every record on an interface is that
	// interface, so an address present in all of them and unique is its own.
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.1.5", "160.79.104.10", ""),
		rec("eni-1", "10.0.1.5", "10.0.9.44", ""),
		rec("eni-1", "10.0.5.11", "10.0.1.5", ""),
	}
	out, stats := InferDirection(in, nil)

	if stats.Inferred != 3 || stats.Undecided != 0 {
		t.Fatalf("stats %+v", stats)
	}
	if out[0].Direction != wire.Egress || out[2].Direction != wire.Ingress {
		t.Fatalf("directions: %v", []wire.Direction{
			out[0].Direction, out[1].Direction, out[2].Direction,
		})
	}
}

func TestAnInterfaceWithOnePeerStaysUndecidedWithoutAttribution(t *testing.T) {
	// Both addresses are in every record and there is nothing to separate
	// them. Guessing would put bytes on the wrong side of the ratio the whole
	// classifier is built on, silently.
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.1.5", "10.0.7.40", ""),
		rec("eni-1", "10.0.7.40", "10.0.1.5", ""),
	}
	out, stats := InferDirection(in, nil)

	if len(out) != 0 || stats.Undecided != 2 {
		t.Fatalf("guessed rather than declined: %d out, stats %+v", len(out), stats)
	}
}

func TestAttributionRescuesTheSinglePeerCase(t *testing.T) {
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.1.5", "10.0.7.40", ""),
		rec("eni-1", "10.0.7.40", "10.0.1.5", ""),
	}
	out, stats := InferDirection(in, map[string]string{"eni-1": "10.0.1.5"})

	if len(out) != 2 || stats.Inferred != 2 {
		t.Fatalf("stats %+v", stats)
	}
}

func TestAttributionIsBelievedOverTheCommonAddress(t *testing.T) {
	// AWS reporting the interface's address beats anything derived from the
	// records, and the two disagree exactly when the derivation is wrong.
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.9.44", "10.0.1.5", ""),
		rec("eni-1", "10.0.5.11", "10.0.1.5", ""),
	}
	out, _ := InferDirection(in, map[string]string{"eni-1": "10.0.9.44"})

	// Derived would say 10.0.1.5 is the interface and call both ingress.
	if out[0].Direction != wire.Egress {
		t.Fatalf("the derived address won: %v", out[0].Direction)
	}
}

func TestARecordWithNeitherEndAtTheInterfaceIsDropped(t *testing.T) {
	// A secondary private IP, or traffic the interface only forwarded. A
	// dropped record costs coverage; a guessed one costs a finding.
	in := []wire.FlowRecord{rec("eni-1", "10.0.2.9", "10.0.3.9", "")}
	out, stats := InferDirection(in, map[string]string{"eni-1": "10.0.1.5"})

	if len(out) != 0 || stats.Undecided != 1 {
		t.Fatalf("%d out, stats %+v", len(out), stats)
	}
}

func TestInterfacesAreInferredIndependently(t *testing.T) {
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.1.5", "160.79.104.10", ""),
		rec("eni-1", "10.0.1.5", "10.0.9.44", ""),
		rec("eni-2", "10.0.2.7", "160.79.104.10", ""),
		rec("eni-2", "10.0.2.7", "10.0.5.11", ""),
	}
	out, stats := InferDirection(in, nil)

	if stats.Inferred != 4 {
		t.Fatalf("stats %+v", stats)
	}
	for _, r := range out {
		if r.Direction != wire.Egress {
			t.Fatalf("%s %s -> %s came out %v", r.InterfaceID, r.SrcAddr, r.DstAddr, r.Direction)
		}
	}
}

func TestDecidedFractionIsWhatTheCoverageNoteNeeds(t *testing.T) {
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.1.5", "160.79.104.10", ""),
		rec("eni-1", "10.0.1.5", "10.0.9.44", ""),
		rec("eni-2", "10.0.2.9", "10.0.3.9", ""),
	}
	_, stats := InferDirection(in, map[string]string{"eni-2": "10.0.4.4"})

	if got := stats.Decided(); got < 0.66 || got > 0.67 {
		t.Fatalf("decided %.2f, stats %+v", got, stats)
	}
	if empty := (DirectionStats{}).Decided(); empty != 1 {
		t.Fatalf("nothing to decide should not read as a failure: %v", empty)
	}
}

func TestInferenceDoesNotDisturbTheInputSlice(t *testing.T) {
	in := []wire.FlowRecord{
		rec("eni-1", "10.0.1.5", "160.79.104.10", ""),
		rec("eni-1", "10.0.1.5", "10.0.9.44", ""),
	}
	InferDirection(in, map[string]string{"eni-1": "10.0.1.5"})

	if in[0].Direction != "" {
		t.Fatal("the caller's records were mutated underneath it")
	}
}
