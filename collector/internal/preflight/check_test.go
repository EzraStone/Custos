package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
	"github.com/EzraStone/Custos/collector/internal/ingest"
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
	return RunWith(context.Background(), cfg, flows, nil, oneRegion{})
}

// oneRegion is an account that runs in the region being scanned and nowhere
// else, which is the uninteresting case and therefore the right default.
type oneRegion struct{}

func (oneRegion) Run(context.Context) ([]ingest.Region, error) {
	return []ingest.Region{{Name: "us-east-1", FlowLogs: 1}}, nil
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

// gatewayTraffic is one workload calling peer and acting on what comes back.
//
// The database leg is not decoration. A workload whose only private
// destination is one address is a log shipper, and preflight stopped naming
// those - so a fixture without a tool loop tests a shape no agent has.
func gatewayTraffic(peer string, out, back int64) []wire.FlowRecord {
	records := oneWayTraffic("eni-1", peer, out, back)
	for i := 0; i < 20; i++ {
		records = append(records,
			wire.FlowRecord{
				InterfaceID: "eni-1", Direction: wire.Egress, DstAddr: "10.0.9.44",
				DstPort: 5432, Bytes: 4_000, SrcAddr: "10.0.1.5", SrcPort: 52000 + i,
			},
			wire.FlowRecord{
				InterfaceID: "eni-1", Direction: wire.Ingress, SrcAddr: "10.0.9.44",
				SrcPort: 5432, Bytes: 30_000, DstAddr: "10.0.1.5", DstPort: 52000 + i,
			},
		)
	}
	return records
}

// oneWayTraffic is a workload that talks to exactly one address: a log
// shipper, a backup agent, a metrics pusher.
func oneWayTraffic(iface, peer string, out, back int64) []wire.FlowRecord {
	var records []wire.FlowRecord
	for i := 0; i < 40; i++ {
		records = append(records,
			wire.FlowRecord{
				InterfaceID: iface, Direction: wire.Egress, DstAddr: peer,
				DstPort: 443, Bytes: out, SrcAddr: "10.0.1.5", SrcPort: 41000 + i,
			},
			wire.FlowRecord{
				InterfaceID: iface, Direction: wire.Ingress, SrcAddr: peer,
				SrcPort: 443, Bytes: back, DstAddr: "10.0.1.5", DstPort: 41000 + i,
			},
		)
	}
	return records
}

// TestAPossibleGatewayIsNamedBeforeAnythingIsSent: the model-traffic check is
// deliberately crude — any outbound 443 — because the full catalogue would
// report a clean pass on exactly the account whose gateway we cannot see. The
// cost is that it passes cleanly there too, saying nothing. This is the other
// half, and it runs before a single byte leaves the account.
func TestAPossibleGatewayIsNamedBeforeAnythingIsSent(t *testing.T) {
	records := append(modelTraffic(1), gatewayTraffic("10.0.7.40", 140_000, 9_000)...)
	report := run(good(), stubFlows{records: records})

	result := find(t, report, "possible model gateway")
	if result.Status != Warn || !strings.Contains(result.Detail, "10.0.7.40") {
		t.Fatalf("got %v, detail %q", result.Status, result.Detail)
	}
	if !strings.Contains(result.Remedy, "custos declare") {
		t.Fatalf("remedy does not say what to do: %q", result.Remedy)
	}
	if !strings.Contains(result.Remedy, "invisible") {
		t.Fatalf("remedy does not say what it costs: %q", result.Remedy)
	}
}

func TestASymmetricInternalApiIsNotNamed(t *testing.T) {
	records := append(modelTraffic(1), gatewayTraffic("10.0.4.23", 40_000, 38_000)...)
	for _, r := range run(good(), stubFlows{records: records}).Results {
		if r.Name == "possible model gateway" {
			t.Fatalf("named an ordinary request/response API: %+v", r)
		}
	}
}

func TestAQuietDestinationIsNotNamed(t *testing.T) {
	// A megabyte over a whole window is a health check.
	records := append(modelTraffic(1), gatewayTraffic("10.0.7.40", 1_000, 10)...)
	for _, r := range run(good(), stubFlows{records: records}).Results {
		if r.Name == "possible model gateway" {
			t.Fatalf("named a health check: %+v", r)
		}
	}
}

func TestADatastorePortIsNeverAGateway(t *testing.T) {
	var records []wire.FlowRecord
	for i := 0; i < 40; i++ {
		records = append(records, wire.FlowRecord{
			Direction: wire.Egress, DstAddr: "10.0.9.44", DstPort: 5432,
			Bytes: 140_000, SrcAddr: "10.0.1.5", SrcPort: 41000 + i,
		})
	}
	records = append(records, modelTraffic(1)...)
	for _, r := range run(good(), stubFlows{records: records}).Results {
		if r.Name == "possible model gateway" {
			t.Fatalf("named a datastore: %+v", r)
		}
	}
}

// TestAnAccountWithNoInternalTrafficIsNotWarned: a warning that fires on a
// healthy account is one that gets ignored on the account where it matters.
func TestAnAccountWithNoInternalTrafficIsNotWarned(t *testing.T) {
	for _, r := range run(good(), stubFlows{records: modelTraffic(5)}).Results {
		if r.Name == "possible model gateway" {
			t.Fatalf("warned on an account with nothing internal: %+v", r)
		}
	}
}

// --- the account's own flow log format ---------------------------------------

// The format an account gets by turning flow logs on and configuring nothing.
const defaultAWSFormat = "version account-id interface-id srcaddr dstaddr " +
	"srcport dstport protocol packets bytes start end action log-status"

func TestOurOwnFormatPassesWithNothingToSay(t *testing.T) {
	report := run(good(), stubFlows{records: modelTraffic(60), stats: flowlogs.Stats{Lines: 60, Parsed: 60}})
	if got := find(t, report, "flow log fields").Status; got != Pass {
		t.Fatalf("status %v", got)
	}
}

func TestTheDefaultAwsFormatWarnsAndSaysWhatItCosts(t *testing.T) {
	// Discovering this from a thin report a week later is the same failure
	// this package exists to prevent: an empty result that reads as clean.
	cfg := good()
	cfg.Format = flowlogs.MustParseFormat(defaultAWSFormat)

	result := find(t, run(cfg, stubFlows{
		records: modelTraffic(60), stats: flowlogs.Stats{Lines: 60, Parsed: 60},
	}), "flow log fields")

	if result.Status != Warn {
		t.Fatalf("status %v", result.Status)
	}
	if !strings.Contains(result.Detail, "flow-direction absent") {
		t.Fatalf("detail does not name the cost: %q", result.Detail)
	}
	if !strings.Contains(result.Remedy, "change to the log, not to Custos") {
		t.Fatalf("remedy does not say where the fix lives: %q", result.Remedy)
	}
}

func TestADegradedFormatDoesNotBlockTheScan(t *testing.T) {
	// It costs recall. Refusing to scan over it would turn a degraded result
	// into no result.
	cfg := good()
	cfg.Format = flowlogs.MustParseFormat(defaultAWSFormat)

	if !run(cfg, stubFlows{records: modelTraffic(60), stats: flowlogs.Stats{Lines: 60, Parsed: 60}}).Ready() {
		t.Fatal("a workable format blocked the scan")
	}
}

func TestAFormatMissingSomethingRequiredBlocks(t *testing.T) {
	cfg := good()
	cfg.Format = flowlogs.MustParseFormat("version interface-id srcaddr dstaddr")

	report := run(cfg, stubFlows{records: modelTraffic(60), stats: flowlogs.Stats{Lines: 60, Parsed: 60}})
	result := find(t, report, "flow log fields")

	if result.Status != Fail {
		t.Fatalf("status %v", result.Status)
	}
	if !strings.Contains(result.Detail, "bytes") {
		t.Fatalf("detail does not name what is missing: %q", result.Detail)
	}
	if report.Ready() {
		t.Fatal("a scan that cannot mean anything was declared ready")
	}
}

// --- IPv6, which the catalogue cannot speak to --------------------------------

func v6Records(dsts ...string) []wire.FlowRecord {
	var out []wire.FlowRecord
	for i, d := range dsts {
		out = append(out, wire.FlowRecord{
			InterfaceID: "eni-1", SrcAddr: "10.0.1.5", DstAddr: d,
			SrcPort: 41000 + i, DstPort: 443, Bytes: 140_000,
			Direction: wire.Egress,
			Start:     time.Unix(1754827200, 0).UTC(),
			End:       time.Unix(1754827259, 0).UTC(),
		})
	}
	return out
}

func TestPublicIPv6DestinationsAreWarnedAboutBeforeTheScan(t *testing.T) {
	// An agent whose model calls go over IPv6 is invisible, so a thin report
	// on a dual-stack account means much less than it looks like it does.
	report := run(good(), stubFlows{
		records: v6Records("2606:4700::1", "2606:4700::2", "2606:4700::1"),
		stats:   flowlogs.Stats{Lines: 3, Parsed: 3},
	})
	result := find(t, report, "ipv6 destinations")

	if result.Status != Warn {
		t.Fatalf("status %v", result.Status)
	}
	if !strings.Contains(result.Detail, "2 public IPv6") {
		t.Fatalf("counted wrong, or not once each: %q", result.Detail)
	}
	if !strings.Contains(result.Remedy, "will not appear in the report") {
		t.Fatalf("remedy does not say what it costs: %q", result.Remedy)
	}
}

func TestPrivateIPv6IsNotAWarning(t *testing.T) {
	// A dual-stack VPC's internal traffic classifies fine. Warning about it
	// would put this on every dual-stack account whether or not it had a
	// blind spot.
	report := run(good(), stubFlows{
		records: v6Records("fd00::1", "fe80::1"),
		stats:   flowlogs.Stats{Lines: 2, Parsed: 2},
	})
	if got := find(t, report, "ipv6 destinations").Status; got != Pass {
		t.Fatalf("status %v", got)
	}
}

func TestIPv6DoesNotBlockAScan(t *testing.T) {
	// It costs recall on some traffic. Refusing to scan would turn a partial
	// result into no result.
	report := run(good(), stubFlows{
		records: append(modelTraffic(60), v6Records("2606:4700::1")...),
		stats:   flowlogs.Stats{Lines: 61, Parsed: 61},
	})
	if !report.Ready() {
		t.Fatal("an account with IPv6 egress was refused a scan")
	}
}

// --- remedies that name the actual fix ----------------------------------------

// "check that the role can read the log group" is true of every read failure
// and useful for none of them. The S3 grant is a Terraform variable most people
// do not know exists, so a denied read of a bucket has to name it.
func TestADeniedBucketReadNamesTheTerraformVariable(t *testing.T) {
	cfg := good()
	cfg.FlowLogs = "s3://acme-log-archive/AWSLogs"

	report := run(cfg, stubFlows{err: errors.New(
		"operation error S3: ListObjectsV2, AccessDenied: Access Denied")})
	result := find(t, report, "flow logs readable")

	if result.Status != Fail {
		t.Fatalf("status %v", result.Status)
	}
	if !strings.Contains(result.Remedy, "log_buckets") {
		t.Fatalf("the remedy does not name the variable: %q", result.Remedy)
	}
}

func TestADeniedLogGroupReadDoesNotSendThemToTheBucketVariable(t *testing.T) {
	cfg := good()
	cfg.FlowLogs = "/aws/vpc/flowlogs"

	remedy := find(t, run(cfg, stubFlows{
		err: errors.New("AccessDeniedException: not authorized to perform logs:FilterLogEvents"),
	}), "flow logs readable").Remedy

	if strings.Contains(remedy, "log_buckets") {
		t.Fatalf("a CloudWatch failure was blamed on an S3 variable: %q", remedy)
	}
	if !strings.Contains(remedy, "log group") {
		t.Fatalf("the remedy does not name what failed: %q", remedy)
	}
}

func TestAReadThatFailedForSomeOtherReasonSaysSo(t *testing.T) {
	remedy := find(t, run(good(), stubFlows{
		err: errors.New("ResourceNotFoundException: log group does not exist"),
	}), "flow logs readable").Remedy

	if strings.Contains(remedy, "log_buckets") || strings.Contains(remedy, "cannot read") {
		t.Fatalf("a missing log group was reported as a permission problem: %q", remedy)
	}
}

// Custos reads whatever format an account has now, so a remedy telling someone
// to apply our Terraform sends them down the expensive path this product
// deliberately stopped requiring.
func TestAnUnparseableLogPointsAtTheFormatVariableNotAtTerraform(t *testing.T) {
	remedy := find(t, run(good(), stubFlows{
		records: modelTraffic(1),
		stats:   flowlogs.Stats{Lines: 400, Malformed: 400},
	}), "flow log format").Remedy

	if !strings.Contains(remedy, "CUSTOS_FLOW_LOG_FORMAT") {
		t.Fatalf("the remedy does not name the setting: %q", remedy)
	}
}

// --- the promise this package makes -------------------------------------------

// Every problem it reports carries something to do about it.
//
// The Result type says so in a comment — "a check that reports a problem
// without one has moved the work rather than done it" — and nothing enforced
// it. A check added later with a blank remedy would leave someone reading
// "FAIL: flow log fields" with no idea what to change, which is the state this
// whole package exists to get people out of.
func TestEveryProblemComesWithSomethingToDo(t *testing.T) {
	// Every shape of failure this package can produce, in one sweep.
	reports := []Report{
		run(good(), stubFlows{err: errors.New("AccessDenied")}),
		run(good(), stubFlows{records: nil, stats: flowlogs.Stats{}}),
		run(good(), stubFlows{
			records: modelTraffic(1),
			stats:   flowlogs.Stats{Lines: 400, Malformed: 400},
		}),
		run(noRole(), stubFlows{records: modelTraffic(60), stats: cleanStats()}),
		run(noAccessLogs(), stubFlows{records: modelTraffic(60), stats: cleanStats()}),
		run(defaultFormatConfig(), stubFlows{
			records: append(modelTraffic(60), v6Records("2606:4700::1")...),
			stats:   cleanStats(),
		}),
		Run(context.Background(), good(), nil, nil),
	}

	seen := map[string]bool{}
	for _, report := range reports {
		for _, result := range report.Results {
			if result.Status == Pass {
				continue
			}
			seen[result.Name] = true
			if strings.TrimSpace(result.Remedy) == "" {
				t.Errorf("%q reports %s with no remedy: %q",
					result.Name, result.Status, result.Detail)
			}
		}
	}

	// Named rather than counted, so that a check added later is absent from
	// this list rather than hidden inside a number that still passes.
	for _, name := range []string{
		"flow logs readable", "flow log format", "flow log fields",
		"access logs", "ipv6 destinations", "aws reachability",
	} {
		if !seen[name] {
			t.Errorf("%q never failed or warned in this sweep, so its remedy "+
				"is untested; add a case that provokes it", name)
		}
	}
}

func cleanStats() flowlogs.Stats {
	return flowlogs.Stats{Lines: 60, Parsed: 60}
}

func noRole() Config {
	cfg := good()
	cfg.RoleARN = ""
	cfg.ExternalID = ""
	return cfg
}

func noAccessLogs() Config {
	cfg := good()
	cfg.AccessLogs = ""
	return cfg
}

func defaultFormatConfig() Config {
	cfg := good()
	cfg.Format = flowlogs.MustParseFormat(defaultAWSFormat)
	return cfg
}

// --- the rest of the account --------------------------------------------------

type regionsWith struct {
	found []ingest.Region
	err   error
}

func (r regionsWith) Run(context.Context) ([]ingest.Region, error) {
	return r.found, r.err
}

func withRegions(cfg Config, regions Regions) Report {
	return RunWith(context.Background(), cfg, stubFlows{
		records: modelTraffic(60), stats: flowlogs.Stats{Lines: 60, Parsed: 60},
	}, nil, regions)
}

// A scan of us-east-1 reports "no unsanctioned agents" about eu-west-1 with
// exactly the confidence it reports it about the region it read.
func TestOtherRegionsWithFlowLogsAreNamed(t *testing.T) {
	result := find(t, withRegions(good(), regionsWith{found: []ingest.Region{
		{Name: "us-east-1", FlowLogs: 2},
		{Name: "eu-west-1", FlowLogs: 1},
		{Name: "ap-south-1", FlowLogs: 3},
	}}), "other regions")

	if result.Status != Warn {
		t.Fatalf("status %v", result.Status)
	}
	if !strings.Contains(result.Detail, "ap-south-1") ||
		!strings.Contains(result.Detail, "eu-west-1") {
		t.Fatalf("the other regions are not named: %q", result.Detail)
	}
	if strings.Contains(result.Detail, "us-east-1") {
		t.Fatalf("the region being scanned was listed as unscanned: %q", result.Detail)
	}
}

func TestARegionWithNoFlowLogsIsNotWorthMentioning(t *testing.T) {
	// AWS enables about seventeen regions by default. Naming every empty one
	// would make this the noisiest line in the report and the first ignored.
	result := find(t, withRegions(good(), regionsWith{found: []ingest.Region{
		{Name: "us-east-1", FlowLogs: 1},
		{Name: "sa-east-1"}, {Name: "af-south-1"}, {Name: "me-central-1"},
	}}), "other regions")

	if result.Status != Pass {
		t.Fatalf("status %v: %q", result.Status, result.Detail)
	}
}

func TestARegionThatCouldNotBeCheckedIsNotSilence(t *testing.T) {
	result := find(t, withRegions(good(), regionsWith{found: []ingest.Region{
		{Name: "us-east-1", FlowLogs: 1},
		{Name: "eu-west-1", Err: errors.New("UnauthorizedOperation")},
	}}), "other regions")

	if result.Status != Warn {
		t.Fatalf("status %v", result.Status)
	}
	if !strings.Contains(result.Detail, "eu-west-1") {
		t.Fatalf("detail: %q", result.Detail)
	}
}

func TestAnAccountThatCannotBeEnumeratedIsNotOneRegion(t *testing.T) {
	result := find(t, withRegions(good(), regionsWith{
		err: errors.New("UnauthorizedOperation: ec2:DescribeRegions"),
	}), "other regions")

	if result.Status != Warn {
		t.Fatalf("status %v", result.Status)
	}
	if !strings.Contains(result.Remedy, "DescribeRegions") {
		t.Fatalf("the remedy does not name the grant: %q", result.Remedy)
	}
}

func TestNoSurveyWiredIsReportedRatherThanAssumed(t *testing.T) {
	result := find(t, withRegions(good(), nil), "other regions")
	if result.Status != Warn {
		t.Fatalf("a scan with no idea how many regions this account uses passed: %v",
			result.Status)
	}
}

func TestTheRemedyNamesTheVariableThatFixesIt(t *testing.T) {
	// It used to say "run a collector per region", which was true when one
	// collector meant one region. Advice that outlives the thing it was
	// working around sends people the long way round.
	result := find(t, withRegions(good(), regionsWith{found: []ingest.Region{
		{Name: "us-east-1", FlowLogs: 1}, {Name: "eu-west-1", FlowLogs: 1},
	}}), "other regions")

	if !strings.Contains(result.Remedy, "CUSTOS_REGIONS") {
		t.Fatalf("the remedy does not name the variable: %q", result.Remedy)
	}
	if strings.Contains(result.Remedy, "collector per region") {
		t.Fatalf("stale advice: %q", result.Remedy)
	}
}

func TestOtherRegionsDoNotBlockAScan(t *testing.T) {
	// One region scanned is a real scan of one region. Blocking it would turn
	// a partial answer into no answer.
	report := withRegions(good(), regionsWith{found: []ingest.Region{
		{Name: "us-east-1", FlowLogs: 1}, {Name: "eu-west-1", FlowLogs: 1},
	}})
	if !report.Ready() {
		t.Fatal("a multi-region account was refused a scan")
	}
}

// TestALogShipperIsNotNamedAsAGateway: the loudest workload in most accounts
// sends half a gigabyte to one collector and gets acknowledgements back. It
// has exactly the shape this check looks for, and naming it is how a customer
// learns on day one that the warning means nothing.
func TestALogShipperIsNotNamedAsAGateway(t *testing.T) {
	records := append(modelTraffic(1), oneWayTraffic("eni-9", "10.0.8.10", 400_000, 6_000)...)
	result := find(t, run(good(), stubFlows{records: records}), "possible model gateway")
	if result.Status != Pass {
		t.Fatalf("named a log shipper: %+v", result)
	}
	if !strings.Contains(result.Detail, "1 one-way sender") {
		t.Fatalf("declining to name it was not disclosed: %q", result.Detail)
	}
}

// TestTheLoudestSuspectIsNamedFirst: the list is cut at three, and it used to
// be cut alphabetically - which throws away the question worth asking to keep
// one about 10.0.0.7.
func TestTheLoudestSuspectIsNamedFirst(t *testing.T) {
	records := modelTraffic(1)
	records = append(records, gatewayTraffic("10.0.9.90", 400_000, 9_000)...)
	records = append(records, gatewayTraffic("10.0.1.10", 100_000, 9_000)...)

	result := find(t, run(good(), stubFlows{records: records}), "possible model gateway")
	if !strings.HasPrefix(result.Detail, "10.0.9.90") {
		t.Fatalf("the loudest was not first: %q", result.Detail)
	}
}

// TestTheRemedySaysWhatMadeItASuspect: "sends more than it receives" describes
// a backup service too. The half that decides is the loop.
func TestTheRemedySaysWhatMadeItASuspect(t *testing.T) {
	records := append(modelTraffic(1), gatewayTraffic("10.0.7.40", 140_000, 9_000)...)
	result := find(t, run(good(), stubFlows{records: records}), "possible model gateway")
	if !strings.Contains(result.Remedy, "tool loop") {
		t.Fatalf("remedy does not say what made it a suspect: %q", result.Remedy)
	}
}
