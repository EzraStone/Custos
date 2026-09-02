package ingest

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"

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

func flow(eni string) wire.FlowRecord {
	return wire.FlowRecord{
		InterfaceID: eni, SrcAddr: "10.0.20.11", DstAddr: "160.79.104.10",
		DstPort: 443, Bytes: 100_000, Direction: wire.Egress,
	}
}

func collector(flows FlowSource, ec2API *fakeEC2, iamAPI *fakeIAM) *Collector {
	c := &Collector{Flows: flows, AccountID: "447120043318", Region: "us-east-1"}
	if ec2API != nil {
		c.Network = ec2API
	}
	if iamAPI != nil {
		c.Identity = iamAPI
	}
	return c
}

func TestCollectAssemblesFlowsAttachmentsAndPrincipals(t *testing.T) {
	flows := stubFlows{
		records: []wire.FlowRecord{flow("eni-1"), flow("eni-1"), flow("eni-2")},
		stats:   flowlogs.Stats{Lines: 3, Parsed: 3},
	}
	ec2API := &fakeEC2{
		interfaces: []ec2types.NetworkInterface{
			eni("eni-1", "primary", onInstance("i-a")),
			eni("eni-2", "primary", onInstance("i-b")),
		},
		instances: map[string]string{
			"i-a": "arn:aws:iam::447120043318:instance-profile/autofix-runner",
			"i-b": "arn:aws:iam::447120043318:instance-profile/finance-close",
		},
	}
	iamAPI := &fakeIAM{encode: true, inline: map[string]string{
		"p": `{"Statement":[{"Effect":"Allow","Action":"s3:PutObject"}]}`,
	}}

	batch, report, err := collector(flows, ec2API, iamAPI).Collect(
		context.Background(), s3Window())
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.Flows) != 3 {
		t.Fatalf("flows lost: %d", len(batch.Flows))
	}
	if len(batch.Attachments) != 2 || len(batch.Principals) != 2 {
		t.Fatalf("attachments=%d principals=%d", len(batch.Attachments), len(batch.Principals))
	}
	if report.Interfaces != 2 {
		t.Fatalf("interface count wrong: %d", report.Interfaces)
	}
	if batch.Principals[0].Compute != "EC2" {
		t.Fatalf("compute not carried: %+v", batch.Principals[0])
	}
}

// TestEachInterfaceIsLookedUpOnce: flow logs repeat an ENI thousands of times
// and each duplicate lookup is a billable call in the customer's account.
func TestEachInterfaceIsLookedUpOnce(t *testing.T) {
	var records []wire.FlowRecord
	for i := 0; i < 5000; i++ {
		records = append(records, flow("eni-1"))
	}
	ec2API := &fakeEC2{interfaces: []ec2types.NetworkInterface{eni("eni-1", "primary")}}

	_, report, err := collector(stubFlows{records: records}, ec2API, nil).Collect(
		context.Background(), s3Window())
	if err != nil {
		t.Fatal(err)
	}
	if report.Interfaces != 1 {
		t.Fatalf("expected 1 distinct interface, got %d", report.Interfaces)
	}
	if ec2API.describes != 1 {
		t.Fatalf("expected 1 describe call, got %d", ec2API.describes)
	}
}

// TestFlowReadErrorDegradesRatherThanAborts: a scan that returns nothing
// because one call failed is a scan the customer does not run twice.
func TestFlowReadErrorDegradesRatherThanAborts(t *testing.T) {
	flows := stubFlows{
		records: []wire.FlowRecord{flow("eni-1")},
		err:     errors.New("throttled"),
	}
	batch, report, err := collector(flows, nil, nil).Collect(context.Background(), s3Window())
	if err != nil {
		t.Fatalf("a partial read must not abort the batch: %v", err)
	}
	if len(batch.Flows) != 1 {
		t.Fatal("partial records should be kept")
	}
	if len(report.Errors) != 1 {
		t.Fatal("the error must be reported, not swallowed")
	}
}

// TestUnreadableRoleStillProducesAFinding: an agent without blast radius is
// worse than one with it, and far better than one that vanished.
func TestUnreadableRoleStillProducesAFinding(t *testing.T) {
	ec2API := &fakeEC2{
		interfaces: []ec2types.NetworkInterface{eni("eni-1", "primary", onInstance("i-a"))},
		instances: map[string]string{
			"i-a": "arn:aws:iam::447120043318:instance-profile/autofix-runner",
		},
	}
	iamAPI := &fakeIAM{getRole: errors.New("AccessDenied")}

	batch, report, err := collector(stubFlows{records: []wire.FlowRecord{flow("eni-1")}},
		ec2API, iamAPI).Collect(context.Background(), s3Window())
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.Principals) != 1 {
		t.Fatal("the principal should still be reported without its policy")
	}
	if len(report.Errors) == 0 {
		t.Fatal("the failure must be surfaced")
	}
}

func TestNoFlowSourceIsAnError(t *testing.T) {
	if _, _, err := (&Collector{}).Collect(context.Background(), s3Window()); err == nil {
		t.Fatal("a collector with no source must say so")
	}
}

func TestReportTrustworthinessReflectsCoverage(t *testing.T) {
	good := Report{Stats: flowlogs.Stats{Lines: 100, Parsed: 100}}
	if !good.Trustworthy() {
		t.Error("full coverage should be trustworthy")
	}
	for name, r := range map[string]Report{
		"truncated": {Stats: flowlogs.Stats{Lines: 100, Parsed: 100, Truncated: true}},
		"skipdata":  {Stats: flowlogs.Stats{Lines: 100, Parsed: 100, SkipData: 3}},
		"lossy":     {Stats: flowlogs.Stats{Lines: 100, Parsed: 50}},
	} {
		if r.Trustworthy() {
			t.Errorf("%s scan must not be reported as trustworthy", name)
		}
	}
}

func TestSummaryWarnsLoudlyAboutUnderRepresentation(t *testing.T) {
	r := Report{
		Stats: flowlogs.Stats{Lines: 100, Parsed: 60, SkipData: 40, Truncated: true},
		Degraded: []Attribution{{
			Attachment: wire.Attachment{InterfaceID: "eni-9", Compute: "EKS"},
			Degraded:   "pod-level attribution needs eBPF",
		}},
	}
	out := r.Summary()
	for _, want := range []string{"SKIPDATA", "under-represented", "record limit", "eni-9"} {
		if !strings.Contains(out, want) {
			t.Errorf("summary missing %q:\n%s", want, out)
		}
	}
}

// A shortened window lost nothing; an unshortened one did. Reporting them the
// same way would either alarm someone over a busy hour or hide real loss.
func TestAShortenedWindowIsNotReportedAsDataLoss(t *testing.T) {
	shortened := Report{
		Stats:     flowlogs.Stats{Lines: 100, Parsed: 100, Truncated: true},
		Shortened: true,
	}.Summary()
	if !strings.Contains(shortened, "no data lost") {
		t.Errorf("a shortened window should say nothing was lost:\n%s", shortened)
	}
	if strings.Contains(shortened, "WARNING") {
		t.Errorf("a shortened window is not a warning:\n%s", shortened)
	}

	lost := Report{
		Stats:     flowlogs.Stats{Lines: 100, Parsed: 100, Truncated: true},
		Shortened: false,
	}.Summary()
	if !strings.Contains(lost, "WARNING") {
		t.Errorf("an unshortened truncation is real loss and must warn:\n%s", lost)
	}
}

// Flow log pages are not strictly ordered. Taking the last record in the slice
// would cut the window short of records already in hand, which would then be
// re-collected forever because the cursor never reaches them.
func TestShortenToUsesTheLatestRecordNotTheLastOne(t *testing.T) {
	early := time.Unix(1786370400, 0).UTC()
	late := time.Unix(1786374000, 0).UTC()

	records := []wire.FlowRecord{
		{End: early}, {End: late}, {End: early.Add(time.Minute)},
	}
	got, ok := ShortenTo(records)
	if !ok || !got.Equal(late) {
		t.Fatalf("got %v (ok=%v), want %v", got, ok, late)
	}
}

func TestShortenToOfNothingIsNotATime(t *testing.T) {
	if _, ok := ShortenTo(nil); ok {
		t.Fatal("an empty read cannot shorten a window")
	}
}

func TestDegradedInterfacesAreListedButCapped(t *testing.T) {
	var degraded []Attribution
	for i := 0; i < 20; i++ {
		degraded = append(degraded, Attribution{
			Attachment: wire.Attachment{InterfaceID: aws.ToString(aws.String("eni-x"))},
			Degraded:   "unknown",
		})
	}
	out := Report{Degraded: degraded}.Summary()
	if !strings.Contains(out, "and 15 more") {
		t.Fatalf("long degraded lists should be capped:\n%s", out)
	}
}

// CloudTrail is the last resort. It fills gaps rather than overriding
// attribution we are more confident in — an ENI attached to an instance
// profile is a stronger claim than an address seen making a Bedrock call,
// because addresses are recycled.
func TestCloudTrailFillsGapsWithoutOverridingStrongerAttribution(t *testing.T) {
	ec2API := &fakeEC2{
		interfaces: []ec2types.NetworkInterface{
			eni("eni-1", "primary", onInstance("i-a")),
			eni("eni-2", "aws-K8S-i-0123456789abcdef0"),
		},
		instances: map[string]string{
			"i-a": "arn:aws:iam::447120043318:instance-profile/known-good",
		},
	}
	trail := &fakeTrail{events: map[string][]string{
		"InvokeModel": {
			rawEvent("10.0.20.11", "", "arn:aws:iam::1:role/should-not-win"),
		},
	}}

	c := collector(stubFlows{records: []wire.FlowRecord{flow("eni-1"), flow("eni-2")}},
		ec2API, nil)
	c.Trail = trail

	batch, report, err := c.Collect(context.Background(), s3Window())
	if err != nil {
		t.Fatal(err)
	}

	byInterface := map[string]string{}
	for _, a := range batch.Attachments {
		byInterface[a.InterfaceID] = a.Principal
	}
	if byInterface["eni-1"] != "arn:aws:iam::447120043318:role/known-good" {
		t.Fatalf("CloudTrail overrode a stronger attribution: %v", byInterface)
	}
	_ = report
}

// Skipping the lookup when nothing needs filling is the difference between
// CloudTrail costing nothing on a well-tagged account and costing a dozen API
// calls every window forever.
func TestCloudTrailIsNotCalledWhenEverythingResolved(t *testing.T) {
	ec2API := &fakeEC2{
		interfaces: []ec2types.NetworkInterface{eni("eni-1", "primary", onInstance("i-a"))},
		instances: map[string]string{
			"i-a": "arn:aws:iam::447120043318:instance-profile/known",
		},
	}
	trail := &fakeTrail{}

	c := collector(stubFlows{records: []wire.FlowRecord{flow("eni-1")}}, ec2API, nil)
	c.Trail = trail

	if _, _, err := c.Collect(context.Background(), s3Window()); err != nil {
		t.Fatal(err)
	}
	if trail.calls != 0 {
		t.Fatalf("CloudTrail was queried with nothing to fill in: %d calls", trail.calls)
	}
}

// A scan leaning heavily on the weakest attribution path is a scan whose
// owners are less certain than the rest, and the summary has to say so.
func TestTrailAttributionIsReportedAsWeaker(t *testing.T) {
	out := Report{TrailResolved: 4}.Summary()
	if !strings.Contains(out, "less certain") {
		t.Fatalf("summary should qualify CloudTrail attribution:\n%s", out)
	}
}

// TestTheSummaryReportsHowMuchOfTheScopeIsNamed: a customer reading a run
// summary should be able to see this without opening a report. It is a number
// they can act on — tag the ENIs — and it is the difference between an
// approval scope someone can read and a list of IP addresses.
func TestTheSummaryReportsHowMuchOfTheScopeIsNamed(t *testing.T) {
	r := Report{Destinations: 2, PeerAddresses: 5}
	if got := r.Summary(); !strings.Contains(got, "named 2 of 5 internal destinations") {
		t.Fatalf("summary did not report naming:\n%s", got)
	}
}

// TestNothingInternalReachedSaysNothing: a run with no internal destinations
// has nothing to report here, and "named 0 of 0" reads as a failure.
func TestNothingInternalReachedSaysNothing(t *testing.T) {
	r := Report{}
	if strings.Contains(r.Summary(), "internal destinations") {
		t.Fatalf("reported on a run with no internal destinations:\n%s", r.Summary())
	}
}
