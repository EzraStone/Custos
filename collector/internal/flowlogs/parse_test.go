package flowlogs

import (
	"strings"
	"testing"
)

const okLine = "5 447120043318 eni-01a2b3c4d 10.0.20.11 160.79.104.10 43112 443 6 214 286432 " +
	"1786370400 1786370460 ACCEPT OK vpc-0a1b2c3d subnet-0ab12345 egress - 18"

func TestParsesAWellFormedLine(t *testing.T) {
	records, stats, err := Parse(strings.NewReader(okLine))
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 || stats.Parsed != 1 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}
	r := records[0]
	if r.Bytes != 286432 || r.DstPort != 443 || r.Direction != "egress" {
		t.Fatalf("unexpected record %+v", r)
	}
	if r.DstAWSService != "" {
		t.Fatalf("dash should decode to empty, got %q", r.DstAWSService)
	}
	if r.Start.Year() != 2026 {
		t.Fatalf("timestamp decoded wrong: %v", r.Start)
	}
}

func TestAwsServiceAnnotationIsKept(t *testing.T) {
	line := strings.Replace(okLine, " - 18", " BEDROCK 18", 1)
	records, _, err := Parse(strings.NewReader(line))
	if err != nil {
		t.Fatal(err)
	}
	if records[0].DstAWSService != "BEDROCK" {
		t.Fatalf("lost the service annotation: %+v", records[0])
	}
}

// TestMalformedLinesAreSkippedNotFatal: a collector that halts on the first bad
// line is a collector the customer disables in week two.
func TestMalformedLinesAreSkippedNotFatal(t *testing.T) {
	input := strings.Join([]string{okLine, "garbage", "5 too few fields", okLine}, "\n")
	records, stats, err := Parse(strings.NewReader(input))
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 || stats.Malformed != 2 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}
}

// TestSkipDataIsCountedNotHidden: SKIPDATA means AWS dropped records it could
// not capture, so the account's traffic is under-represented. A scan with high
// SKIPDATA is a scan whose absence of findings means less, and the customer
// has to be told.
func TestSkipDataIsCountedNotHidden(t *testing.T) {
	skip := strings.Replace(okLine, " ACCEPT OK ", " - SKIPDATA ", 1)
	nodata := strings.Replace(okLine, " ACCEPT OK ", " - NODATA ", 1)
	_, stats, err := Parse(strings.NewReader(strings.Join([]string{okLine, skip, nodata}, "\n")))
	if err != nil {
		t.Fatal(err)
	}
	if stats.SkipData != 1 || stats.NoData != 1 || stats.Parsed != 1 {
		t.Fatalf("stats wrong: %+v", stats)
	}
	if got := stats.Coverage(); got < 0.33 || got > 0.34 {
		t.Fatalf("coverage should reflect the loss, got %.2f", got)
	}
}

func TestHeaderAndBlankLinesAreIgnored(t *testing.T) {
	input := "version account-id interface-id\n\n" + okLine + "\n\n"
	records, stats, err := Parse(strings.NewReader(input))
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 || stats.Lines != 1 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}
}

func TestCoverageOfAnEmptyStreamIsZeroNotOne(t *testing.T) {
	_, stats, _ := Parse(strings.NewReader(""))
	if stats.Coverage() != 0 {
		t.Fatal("an empty stream must not report full coverage")
	}
}

func TestOverlongLineDoesNotPanic(t *testing.T) {
	if _, _, err := Parse(strings.NewReader(strings.Repeat("x", 2<<20))); err == nil {
		t.Log("overlong line handled without panic")
	}
}
