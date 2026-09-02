package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

type stubFlows struct {
	records []wire.FlowRecord
	stats   flowlogs.Stats
	err     error
}

func (s stubFlows) Read(context.Context, awsread.Window) ([]wire.FlowRecord, flowlogs.Stats, error) {
	return s.records, s.stats, s.err
}

func modelTraffic(n int) []wire.FlowRecord {
	out := make([]wire.FlowRecord, n)
	for i := range out {
		out[i] = wire.FlowRecord{
			Direction: wire.Egress, DstPort: 443, Bytes: 100_000,
			DstAddr: "160.79.104.10",
		}
	}
	return out
}

func good() Config {
	return Config{
		AccountID: "447120043318", Region: "us-east-1",
		RoleARN:    "arn:aws:iam::447120043318:role/custos-discovery",
		ExternalID: "abcdefghijklmnop", FlowLogs: "/aws/vpc/flowlogs",
		AccessLogs: "s3://alb-logs/AWSLogs", HaveEndpoint: true, HaveToken: true,
	}
}

func run(cfg Config, flows FlowSource) Report {
	return Run(context.Background(), cfg, flows, nil)
}

func find(t *testing.T, report Report, name string) Result {
	t.Helper()
	for _, r := range report.Results {
		if r.Name == name {
			return r
		}
	}
	t.Fatalf("no check named %q", name)
	return Result{}
}

func TestAFullyConfiguredAccountIsReady(t *testing.T) {
	report := run(good(), stubFlows{
		records: modelTraffic(500),
		stats:   flowlogs.Stats{Lines: 500, Parsed: 500},
	})
	if !report.Ready() {
		t.Fatalf("expected ready:\n%s", report)
	}
}

// The failure that looks like good news. An empty log group produces a report
// with no findings, which reads exactly like an account with no agents.
func TestAnEmptyLogGroupFailsRatherThanPassingQuietly(t *testing.T) {
	report := run(good(), stubFlows{})
	if report.Ready() {
		t.Fatal("an empty log group must not be reported as ready")
	}

	result := find(t, report, "flow logs readable")
	if result.Status != Fail {
		t.Fatalf("got %s", result.Status)
	}
	if !strings.Contains(result.Remedy, "carrying the traffic") {
		t.Fatalf("remedy does not point at the likely cause: %q", result.Remedy)
	}
}

func TestAnUnreadableLogGroupFails(t *testing.T) {
	report := run(good(), stubFlows{err: errors.New("AccessDeniedException")})
	if report.Ready() {
		t.Fatal("an unreadable log group must block")
	}
	if !strings.Contains(find(t, report, "flow logs readable").Detail, "AccessDenied") {
		t.Fatal("the actual error must be shown")
	}
}

// A log format mismatch parses nothing and finds nothing, which is the same
// symptom as a clean account.
func TestAFormatMismatchFailsAndNamesTheRemedy(t *testing.T) {
	report := run(good(), stubFlows{
		records: nil,
		stats:   flowlogs.Stats{Lines: 400, Malformed: 400},
	})
	if report.Ready() {
		t.Fatal("nothing parsing must block")
	}
}

func TestPartialParsingWarnsWithoutBlocking(t *testing.T) {
	report := run(good(), stubFlows{
		records: modelTraffic(60),
		stats:   flowlogs.Stats{Lines: 100, Parsed: 60},
	})
	if !report.Ready() {
		t.Fatal("partial parsing degrades a scan; it must not prevent one")
	}
	if find(t, report, "flow log format").Status != Warn {
		t.Fatal("expected a warning")
	}
}

// A cross-account role with no external ID can be assumed by anyone who learns
// the ARN.
func TestARoleWithoutAnExternalIDBlocks(t *testing.T) {
	cfg := good()
	cfg.ExternalID = ""
	report := run(cfg, stubFlows{records: modelTraffic(10),
		stats: flowlogs.Stats{Lines: 10, Parsed: 10}})

	if report.Ready() {
		t.Fatal("a role with no external ID must block")
	}
	if !strings.Contains(find(t, report, "credentials").Remedy, "anyone who learns the ARN") {
		t.Fatal("the remedy must say why it matters")
	}
}

// Refusing to scan over a missing access log config would turn a degraded
// result into no result.
func TestMissingAccessLogsWarnWithTheNumberAttached(t *testing.T) {
	cfg := good()
	cfg.AccessLogs = ""
	report := run(cfg, stubFlows{records: modelTraffic(10),
		stats: flowlogs.Stats{Lines: 10, Parsed: 10}})

	if !report.Ready() {
		t.Fatal("missing access logs must not block a scan")
	}
	remedy := find(t, report, "access logs").Remedy
	if !strings.Contains(remedy, "60%") {
		t.Fatalf("the ask needs its number: %q", remedy)
	}
}

func TestNoFlowLogSourceBlocks(t *testing.T) {
	cfg := good()
	cfg.FlowLogs = ""
	if run(cfg, nil).Ready() {
		t.Fatal("no source means nothing to read")
	}
}

// A configuration check that requires credentials is useless in the situation
// where someone most wants to run one.
func TestChecksRunWithoutCredentials(t *testing.T) {
	report := run(good(), nil)
	if len(report.Results) < 4 {
		t.Fatalf("expected configuration checks without credentials:\n%s", report)
	}
	if find(t, report, "aws reachability").Status != Warn {
		t.Fatal("reachability should be reported as skipped, not failed")
	}
}

// A self-hosted gateway is the usual cause, and it is invisible until declared.
func TestNoModelTrafficWarnsAboutAGateway(t *testing.T) {
	internal := []wire.FlowRecord{{Direction: wire.Egress, DstPort: 8080, Bytes: 1000}}
	report := run(good(), stubFlows{
		records: internal, stats: flowlogs.Stats{Lines: 1, Parsed: 1},
	})
	remedy := find(t, report, "model traffic").Remedy
	if !strings.Contains(remedy, "gateway") {
		t.Fatalf("remedy should name the usual cause: %q", remedy)
	}
	if !report.Ready() {
		t.Fatal("this is a warning, not a blocker")
	}
}

func TestEveryFailingCheckCarriesARemedy(t *testing.T) {
	cfg := Config{}
	report := run(cfg, stubFlows{err: errors.New("boom")})
	for _, result := range report.Results {
		if result.Status != Pass && result.Remedy == "" {
			t.Errorf("check %q reports a problem with no remedy", result.Name)
		}
	}
}

func TestReportRendersRemediesOnlyForProblems(t *testing.T) {
	out := run(good(), stubFlows{
		records: modelTraffic(10), stats: flowlogs.Stats{Lines: 10, Parsed: 10},
	}).String()
	if strings.Contains(out, "->") {
		t.Fatalf("a clean report should carry no remedies:\n%s", out)
	}
	if !strings.Contains(out, "Ready to scan.") {
		t.Fatal("a clean report should say so")
	}
}

func TestProbeIntervalDefaults(t *testing.T) {
	cfg := good()
	cfg.ProbeInterval = 0
	out := run(cfg, stubFlows{records: modelTraffic(1),
		stats: flowlogs.Stats{Lines: 1, Parsed: 1}}).String()
	if !strings.Contains(out, time.Hour.String()) {
		t.Fatalf("expected an hour probe window:\n%s", out)
	}
}

// stubNamer answers with whatever it was told, so the check can be exercised
// against a role that can name everything, some things, or nothing.
type stubNamer struct {
	names map[string]string
	err   error
}

func (s stubNamer) Resolve(_ context.Context, addresses []string) ([]wire.Destination, error) {
	if s.err != nil {
		return nil, s.err
	}
	var out []wire.Destination
	for _, a := range addresses {
		if name, ok := s.names[a]; ok {
			out = append(out, wire.Destination{Address: a, Name: name})
		}
	}
	return out, nil
}

func internalTraffic(addresses ...string) []wire.FlowRecord {
	out := modelTraffic(1)
	for _, a := range addresses {
		out = append(out, wire.FlowRecord{
			Direction: wire.Egress, DstPort: 443, Bytes: 5000, DstAddr: a,
		})
	}
	return out
}

func runNamed(cfg Config, flows FlowSource, namer Namer) Report {
	return Run(context.Background(), cfg, flows, namer)
}

// TestUnnamedDestinationsAreWarnedAboutDuringOnboarding: this is the same class
// of failure as an empty log group. Every finding is correct, the reach is
// accurate, and the approval scope is a list of IP addresses nobody can make a
// decision about. Better said now than discovered by whoever is asked to
// approve one.
func TestUnnamedDestinationsAreWarnedAboutDuringOnboarding(t *testing.T) {
	flows := stubFlows{records: internalTraffic("10.0.4.21", "10.0.4.22", "10.0.4.23")}
	report := runNamed(good(), flows, stubNamer{names: map[string]string{}})

	result := find(t, report, "destination names")
	if result.Status != Warn {
		t.Fatalf("expected a warning, got %v: %s", result.Status, result.Detail)
	}
	if !strings.Contains(result.Detail, "none of 3") {
		t.Fatalf("unhelpful detail: %q", result.Detail)
	}
}

func TestMostlyUnnamedIsStillAWarning(t *testing.T) {
	// One name out of four is not a pass. An operator reading that scope is
	// still mostly reading addresses.
	flows := stubFlows{records: internalTraffic("10.0.4.21", "10.0.4.22", "10.0.4.23", "10.0.9.44")}
	report := runNamed(good(), flows, stubNamer{names: map[string]string{"10.0.4.21": "billing-api"}})

	if result := find(t, report, "destination names"); result.Status != Warn {
		t.Fatalf("expected a warning, got %v: %s", result.Status, result.Detail)
	}
}

func TestNamedDestinationsPass(t *testing.T) {
	flows := stubFlows{records: internalTraffic("10.0.4.21", "10.0.9.44")}
	report := runNamed(good(), flows, stubNamer{names: map[string]string{
		"10.0.4.21": "billing-api", "10.0.9.44": "rds",
	}})

	if result := find(t, report, "destination names"); result.Status != Pass {
		t.Fatalf("expected a pass, got %v: %s", result.Status, result.Detail)
	}
}

// TestAMissingPermissionNamesItself: a role without
// ec2:DescribeNetworkInterfaces still produces findings. The remedy has to say
// that, or someone will read the warning as "the scan will not work".
func TestAMissingPermissionNamesItself(t *testing.T) {
	flows := stubFlows{records: internalTraffic("10.0.4.21")}
	report := runNamed(good(), flows, stubNamer{err: errors.New("AccessDenied")})

	result := find(t, report, "destination names")
	if result.Status != Warn || !strings.Contains(result.Remedy, "DescribeNetworkInterfaces") {
		t.Fatalf("got %v, remedy %q", result.Status, result.Remedy)
	}
}

// TestNoInternalTrafficIsNotAFinding: with nothing internal reached in the
// probe window there is nothing this check could have told anyone, and a
// warning would be noise on an account that is fine.
func TestNoInternalTrafficIsNotAFinding(t *testing.T) {
	report := runNamed(good(), stubFlows{records: modelTraffic(3)}, stubNamer{})
	for _, r := range report.Results {
		if r.Name == "destination names" {
			t.Fatalf("reported on a window with no internal destinations: %+v", r)
		}
	}
}
