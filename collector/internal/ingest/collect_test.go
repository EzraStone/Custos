package ingest

import (
	"context"
	"errors"
	"strings"
	"testing"

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
	for _, want := range []string{"SKIPDATA", "under-represented", "event limit", "eni-9"} {
		if !strings.Contains(out, want) {
			t.Errorf("summary missing %q:\n%s", want, out)
		}
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
