package flowlogs

import (
	"strings"
	"testing"
)

const okLine = "5 447120043318 eni-01a2b3c4d 10.0.20.11 160.79.104.10 43112 443 6 214 286432 " +
	"1786370400 1786370460 ACCEPT OK vpc-0a1b2c3d subnet-0ab12345 egress - - 18"

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
	line := strings.Replace(okLine, " - - 18", " - BEDROCK 18", 1)
	records, _, err := Parse(strings.NewReader(line))
	if err != nil {
		t.Fatal(err)
	}
	if records[0].DstAWSService != "BEDROCK" || records[0].SrcAWSService != "" {
		t.Fatalf("lost the service annotation: %+v", records[0])
	}
}

// TestTheSourceAnnotationIsKept: on the return leg of a conversation with an
// AWS service the service is named on the source, not the destination. A
// collector that reads only the destination annotation sees half of every AWS
// conversation as traffic to an unrecognised address.
func TestTheSourceAnnotationIsKept(t *testing.T) {
	line := strings.Replace(okLine, " egress - - 18", " ingress S3 - 18", 1)
	records, _, err := Parse(strings.NewReader(line))
	if err != nil {
		t.Fatal(err)
	}
	if records[0].SrcAWSService != "S3" || records[0].DstAWSService != "" {
		t.Fatalf("lost the source annotation: %+v", records[0])
	}
}

// TestTheTwoAnnotationsDoNotSwap: they are adjacent fields carrying the same
// kind of value, so a transposition would be invisible in every other test.
func TestTheTwoAnnotationsDoNotSwap(t *testing.T) {
	line := strings.Replace(okLine, " - - 18", " ELASTICACHE BEDROCK 18", 1)
	records, _, err := Parse(strings.NewReader(line))
	if err != nil {
		t.Fatal(err)
	}
	if records[0].SrcAWSService != "ELASTICACHE" || records[0].DstAWSService != "BEDROCK" {
		t.Fatalf("annotations transposed: %+v", records[0])
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
	input := LogFormatHeader + "\n\n" + okLine + "\n\n"
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

// --- reading a format we did not configure -----------------------------------

// The format an account gets by turning flow logs on and configuring nothing.
// It is what most existing log archives contain, and the reason a customer can
// point Custos at logs they already pay for.
const awsDefaultFormat = "version account-id interface-id srcaddr dstaddr " +
	"srcport dstport protocol packets bytes start end action log-status"

const awsDefaultLine = "2 447120043318 eni-0a1b2c3d 10.0.1.5 160.79.104.10 " +
	"41000 443 6 40 140000 1754827200 1754827259 ACCEPT OK"

func TestARecordInTheDefaultAwsFormatIsRead(t *testing.T) {
	records, stats, err := ParseFormatted(
		strings.NewReader(awsDefaultLine), MustParseFormat(awsDefaultFormat),
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 || stats.Parsed != 1 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}

	r := records[0]
	if r.InterfaceID != "eni-0a1b2c3d" || r.DstAddr != "160.79.104.10" {
		t.Fatalf("fields landed in the wrong columns: %+v", r)
	}
	if r.Bytes != 140000 || r.DstPort != 443 {
		t.Fatalf("numbers landed in the wrong columns: %+v", r)
	}
	if r.Start.Unix() != 1754827200 {
		t.Fatalf("timestamp: %v", r.Start)
	}
}

func TestFieldsTheFormatDoesNotCarryComeBackEmptyNotWrong(t *testing.T) {
	// The failure this guards against is worse than a missing value: reading
	// position 16 of a 14-field line, or silently taking whatever is there.
	records, _, _ := ParseFormatted(
		strings.NewReader(awsDefaultLine), MustParseFormat(awsDefaultFormat),
	)
	r := records[0]
	if r.Direction != "" {
		t.Fatalf("direction invented: %q", r.Direction)
	}
	if r.DstAWSService != "" || r.SrcAWSService != "" {
		t.Fatalf("AWS service invented: %q / %q", r.SrcAWSService, r.DstAWSService)
	}
	if r.TCPFlags != 0 || r.VpcID != "" {
		t.Fatalf("absent fields did not come back zero: %+v", r)
	}
}

func TestAFieldOrderWeHaveNeverSeenIsReadCorrectly(t *testing.T) {
	// Nothing requires a customer's format to resemble ours. If fields were
	// still being read by position this would decode into the wrong columns
	// and report a clean account.
	format := MustParseFormat("bytes end start dstaddr srcaddr interface-id")
	line := "140000 1754827259 1754827200 160.79.104.10 10.0.1.5 eni-0a1b2c3d"

	records, _, err := ParseFormatted(strings.NewReader(line), format)
	if err != nil || len(records) != 1 {
		t.Fatalf("records %d err %v", len(records), err)
	}
	r := records[0]
	if r.SrcAddr != "10.0.1.5" || r.DstAddr != "160.79.104.10" || r.Bytes != 140000 {
		t.Fatalf("decoded into the wrong columns: %+v", r)
	}
}

func TestTheHeaderInTheFileWinsOverTheConfiguredFormat(t *testing.T) {
	// A stale --flow-log-format setting is otherwise a silent misparse of
	// every line rather than an error. The file is what was actually written.
	input := awsDefaultFormat + "\n" + awsDefaultLine

	records, _, err := ParseFormatted(strings.NewReader(input), Default)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 {
		t.Fatalf("the configured format was believed over the file: %d records", len(records))
	}
	if records[0].Bytes != 140000 {
		t.Fatalf("decoded with the wrong format: %+v", records[0])
	}
}

func TestAFormatMissingSomethingRequiredIsRefusedBeforeParsing(t *testing.T) {
	// Loudly, and before any line is read. A parse that produced zero records
	// would look exactly like an account with no traffic.
	_, _, err := ParseFormatted(
		strings.NewReader(awsDefaultLine),
		MustParseFormat("version account-id interface-id srcaddr dstaddr"),
	)
	if err == nil {
		t.Fatal("a format with no byte counts was parsed rather than refused")
	}
	if !strings.Contains(err.Error(), "bytes") {
		t.Fatalf("the error does not name what is missing: %v", err)
	}
}

func TestAHeaderMissingSomethingRequiredStopsTheRead(t *testing.T) {
	input := "version account-id interface-id srcaddr dstaddr\n1 2 3 4 5"
	if _, _, err := ParseFormatted(strings.NewReader(input), Default); err == nil {
		t.Fatal("a log whose own header is unusable was read anyway")
	}
}

func TestAFormatWithoutLogStatusTreatsEveryLineAsARecord(t *testing.T) {
	// The only reading available. That it overstates coverage is what
	// Degradations exists to say.
	format := MustParseFormat("interface-id srcaddr dstaddr bytes start end")
	line := "eni-0a1 10.0.1.5 10.0.7.40 140000 1754827200 1754827259"

	_, stats, err := ParseFormatted(strings.NewReader(line), format)
	if err != nil {
		t.Fatal(err)
	}
	if stats.Parsed != 1 || stats.NoData != 0 {
		t.Fatalf("stats %+v", stats)
	}
}
