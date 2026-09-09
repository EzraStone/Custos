package flowlogs

import (
	"fmt"
	"strings"
	"testing"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

// The claim this file exists to check: an account that points Custos at the
// flow log it already keeps gets the same traffic as one that runs our
// Terraform module.
//
// It is checked by rendering the same records twice, once in each format, and
// reading both back. Anything that decodes differently is a finding the
// customer would not get, or one they would get about the wrong destination —
// and neither is visible from a report, because both produce a plausible
// document.

type sample struct {
	eni       string
	src, dst  string
	dstPort   int
	bytes     int64
	direction wire.Direction
	awsSvc    string
}

// Two interfaces, several peers each, both directions, and one AWS-annotated
// conversation. Several peers matters: it is what lets direction inference
// answer without attribution.
var samples = []sample{
	{"eni-0a1", "10.0.1.5", "160.79.104.10", 443, 140_000, wire.Egress, ""},
	{"eni-0a1", "160.79.104.10", "10.0.1.5", 41000, 9_000, wire.Ingress, ""},
	{"eni-0a1", "10.0.1.5", "10.0.5.11", 8931, 22_000, wire.Egress, ""},
	{"eni-0a1", "10.0.1.5", "52.216.10.7", 443, 4_000, wire.Egress, "S3"},
	{"eni-0b2", "10.0.2.7", "10.0.7.40", 443, 900_000, wire.Egress, ""},
	{"eni-0b2", "10.0.7.40", "10.0.2.7", 55012, 30_000, wire.Ingress, ""},
	{"eni-0b2", "10.0.2.7", "10.0.9.44", 5432, 60_000, wire.Egress, ""},
}

const (
	sampleStart = 1754827200
	sampleEnd   = 1754827259
)

func (s sample) custosLine() string {
	svc := s.awsSvc
	if svc == "" {
		svc = "-"
	}
	return fmt.Sprintf(
		"5 447120043318 %s %s %s 41000 %d 6 40 %d %d %d ACCEPT OK "+
			"vpc-0a1b2c3d subnet-0ab12345 %s - %s 19",
		s.eni, s.src, s.dst, s.dstPort, s.bytes, sampleStart, sampleEnd,
		s.direction, svc,
	)
}

// The 2014 default: fourteen fields, no direction, no service annotation, no
// vpc or subnet.
func (s sample) defaultLine() string {
	return fmt.Sprintf(
		"2 447120043318 %s %s %s 41000 %d 6 40 %d %d %d ACCEPT OK",
		s.eni, s.src, s.dst, s.dstPort, s.bytes, sampleStart, sampleEnd,
	)
}

const defaultFormatSpec = "version account-id interface-id srcaddr dstaddr " +
	"srcport dstport protocol packets bytes start end action log-status"

func render(lines func(sample) string) string {
	var b strings.Builder
	for _, s := range samples {
		b.WriteString(lines(s))
		b.WriteString("\n")
	}
	return b.String()
}

func TestTheDefaultFormatReadsTheSameTrafficAsOurs(t *testing.T) {
	ours, _, err := ParseFormatted(strings.NewReader(render(sample.custosLine)), Default)
	if err != nil {
		t.Fatal(err)
	}
	theirs, _, err := ParseFormatted(
		strings.NewReader(render(sample.defaultLine)),
		MustParseFormat(defaultFormatSpec),
	)
	if err != nil {
		t.Fatal(err)
	}
	theirs, stats := InferDirection(theirs, nil)

	if len(ours) != len(samples) || len(theirs) != len(samples) {
		t.Fatalf("read %d and %d of %d records", len(ours), len(theirs), len(samples))
	}
	if stats.Undecided != 0 {
		t.Fatalf("direction undecided for %d records", stats.Undecided)
	}

	for i := range ours {
		a, b := ours[i], theirs[i]
		// Everything the classifier reads.
		if a.InterfaceID != b.InterfaceID || a.SrcAddr != b.SrcAddr ||
			a.DstAddr != b.DstAddr || a.DstPort != b.DstPort ||
			a.Bytes != b.Bytes || !a.Start.Equal(b.Start) || !a.End.Equal(b.End) {
			t.Fatalf("record %d differs:\n ours: %+v\ntheirs: %+v", i, a, b)
		}
		if a.Direction != b.Direction {
			t.Fatalf("record %d direction: ours %q, inferred %q",
				i, a.Direction, b.Direction)
		}
	}
}

func TestTheDifferenceIsExactlyWhatWeSayItIs(t *testing.T) {
	// The AWS service annotation is genuinely absent from the default format,
	// and the report says so. Asserted here so that if it ever starts arriving
	// by some accident, the claim in the report becomes wrong loudly.
	theirs, _, err := ParseFormatted(
		strings.NewReader(render(sample.defaultLine)),
		MustParseFormat(defaultFormatSpec),
	)
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range theirs {
		if r.DstAWSService != "" || r.SrcAWSService != "" {
			t.Fatalf("an AWS service annotation appeared from nowhere: %+v", r)
		}
	}

	ours, _, _ := ParseFormatted(strings.NewReader(render(sample.custosLine)), Default)
	var annotated int
	for _, r := range ours {
		if r.DstAWSService != "" {
			annotated++
		}
	}
	if annotated == 0 {
		t.Fatal("the fixture has no annotated conversation, so this proves nothing")
	}
}

func TestAHeaderedDefaultLogNeedsNoConfiguration(t *testing.T) {
	// What an S3 archive actually contains: AWS writes the field names first.
	input := defaultFormatSpec + "\n" + render(sample.defaultLine)

	records, _, err := ParseFormatted(strings.NewReader(input), Default)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != len(samples) {
		t.Fatalf("read %d of %d records", len(records), len(samples))
	}
}
