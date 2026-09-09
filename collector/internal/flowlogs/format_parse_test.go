package flowlogs

import (
	"strings"
	"testing"
)

func TestTheTerraformSpellingIsAccepted(t *testing.T) {
	f, err := ParseFormat("${version} ${account-id} ${srcaddr}")
	if err != nil {
		t.Fatal(err)
	}
	if got := f.Fields(); strings.Join(got, " ") != "version account-id srcaddr" {
		t.Fatalf("fields: %v", got)
	}
}

// AWS writes this as the first line of every flow log object delivered to S3,
// which means a customer never has to tell us their format for S3 delivery.
func TestTheHeaderLineSpellingIsAccepted(t *testing.T) {
	f, err := ParseFormat("version account-id interface-id srcaddr dstaddr")
	if err != nil {
		t.Fatal(err)
	}
	if f.Count() != 5 || !f.Has("interface-id") {
		t.Fatalf("fields: %v", f.Fields())
	}
}

func TestAFieldNameWeHaveNeverHeardOfIsKeptInPlace(t *testing.T) {
	// AWS adds fields to the schema periodically. Refusing a format because it
	// contains one we do not know would break on a customer who configured
	// theirs after we shipped.
	f, err := ParseFormat("srcaddr ecs-container-id dstaddr")
	if err != nil {
		t.Fatal(err)
	}
	if f.index["dstaddr"] != 2 {
		t.Fatalf("the unknown field displaced the ones after it: %v", f.index)
	}
}

func TestADuplicatedFieldIsRefused(t *testing.T) {
	// One of the two positions would be silently ignored, and which one would
	// depend on iteration order rather than on anything the customer intended.
	if _, err := ParseFormat("srcaddr dstaddr srcaddr"); err == nil {
		t.Fatal("a duplicated field name was accepted")
	}
}

func TestSomethingThatIsNotAFormatIsRefused(t *testing.T) {
	for _, spec := range []string{"", "   ", "SELECT * FROM logs", "srcaddr,dstaddr"} {
		if _, err := ParseFormat(spec); err == nil {
			t.Fatalf("accepted %q as a flow log format", spec)
		}
	}
}

func TestTheShippedFormatIsUsableAndDegradesInNoWay(t *testing.T) {
	f := MustParseFormat(LogFormat)
	if !f.Usable() {
		t.Fatalf("the format we ship is missing %v", f.Missing())
	}
	if d := f.Degradations(); len(d) != 0 {
		t.Fatalf("the format we ship degrades: %v", d)
	}
}

// The format AWS gives an account that turned flow logs on without configuring
// anything. It is what most existing log archives contain.
const defaultV2 = "version account-id interface-id srcaddr dstaddr srcport " +
	"dstport protocol packets bytes start end action log-status"

func TestTheDefaultAwsFormatIsUsable(t *testing.T) {
	f := MustParseFormat(defaultV2)
	if !f.Usable() {
		t.Fatalf("the default AWS format is missing %v", f.Missing())
	}
}

func TestTheDefaultAwsFormatSaysWhatItCosts(t *testing.T) {
	// The point of this list is that a customer sees it before the scan rather
	// than wondering afterwards why nothing was identified as an MCP server.
	d := strings.Join(MustParseFormat(defaultV2).Degradations(), "\n")

	if !strings.Contains(d, "flow-direction absent") {
		t.Fatalf("direction inference not disclosed:\n%s", d)
	}
	if !strings.Contains(d, "pkt-dst-aws-service absent") {
		t.Fatalf("unnamed AWS services not disclosed:\n%s", d)
	}
	if strings.Contains(d, "dstport absent") {
		t.Fatalf("the default format has ports:\n%s", d)
	}
}

func TestAFormatWithoutBytesIsNotUsable(t *testing.T) {
	// The entire classifier signal is byte asymmetry. Without it this is not a
	// worse scan, it is a different product.
	f := MustParseFormat("version interface-id srcaddr dstaddr start end")
	if f.Usable() {
		t.Fatal("a format with no byte counts was accepted as usable")
	}
	if got := f.Missing(); len(got) != 1 || got[0] != "bytes" {
		t.Fatalf("missing: %v", got)
	}
}

func TestDegradationsAreStableAcrossRuns(t *testing.T) {
	f := MustParseFormat("interface-id srcaddr dstaddr bytes start end")
	first := strings.Join(f.Degradations(), "|")
	for i := 0; i < 20; i++ {
		if got := strings.Join(f.Degradations(), "|"); got != first {
			t.Fatalf("map iteration order leaked into the output:\n%s\n%s", first, got)
		}
	}
}

func TestAHeaderLineIsRecognised(t *testing.T) {
	if !IsHeader(defaultV2) {
		t.Fatal("the header line was not recognised")
	}
}

func TestARecordIsNotMistakenForAHeader(t *testing.T) {
	// No flow log record contains the literal "srcaddr"; a header always does,
	// because AWS refuses a format that omits it.
	record := "2 447120043318 eni-0a1 10.0.1.5 160.79.104.10 41000 443 6 " +
		"40 140000 1754827200 1754827259 ACCEPT OK"
	if IsHeader(record) {
		t.Fatal("a data line was treated as a header")
	}
}
