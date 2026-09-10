package main

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsclient"
	"github.com/EzraStone/Custos/collector/internal/config"
	"github.com/EzraStone/Custos/collector/internal/ingest"
)

// Access logs are region-specific: AWS writes them under
// AWSLogs/<account>/elasticloadbalancing/<region>/. Reading one region's while
// classifying another's traffic is worse than reading none — every model call
// that happened to line up with an unrelated inbound request would look
// coupled, which is the shape of a chatbot. The agents this product exists to
// find would be classified as not agents.

func cfgWith(accessLogs, region string) *config.Config {
	return &config.Config{
		AccountID: "447120043318", Region: "us-east-1", AccessLogs: accessLogs,
	}
}

func TestABucketAloneGetsThePerRegionPathDerived(t *testing.T) {
	source, note := accessLogSource(
		cfgWith("s3://acme-alb-logs", ""), &awsclient.Clients{}, "eu-west-1",
	)
	if note != "" {
		t.Fatalf("unexpected note: %q", note)
	}
	reader, ok := source.(*ingest.AccessLogReader)
	if !ok {
		t.Fatalf("no reader: %T", source)
	}
	if reader.Prefix != "AWSLogs/447120043318/elasticloadbalancing/eu-west-1" {
		t.Fatalf("prefix %q", reader.Prefix)
	}
}

func TestAFixedPrefixIsUsedForTheRegionItWasWrittenFor(t *testing.T) {
	source, note := accessLogSource(
		cfgWith("s3://acme-alb-logs/AWSLogs/1/elasticloadbalancing/us-east-1", ""),
		&awsclient.Clients{}, "us-east-1",
	)
	if source == nil || note != "" {
		t.Fatalf("source %v note %q", source, note)
	}
}

func TestAFixedPrefixIsNotReadForAnotherRegion(t *testing.T) {
	source, note := accessLogSource(
		cfgWith("s3://acme-alb-logs/AWSLogs/1/elasticloadbalancing/us-east-1", ""),
		&awsclient.Clients{}, "eu-west-1",
	)
	if source != nil {
		t.Fatal("one region's access logs were read while classifying another's traffic")
	}
	if !strings.Contains(note, "eu-west-1") {
		t.Fatalf("the note does not say which region lost recall: %q", note)
	}
	if !strings.Contains(note, "s3://bucket") {
		t.Fatalf("the note does not say how to fix it: %q", note)
	}
}

func TestNoAccessLogsConfiguredIsNotANote(t *testing.T) {
	// A supported state, reported by the classifier as reduced recall. A note
	// on every run for a choice the customer already made is noise.
	source, note := accessLogSource(cfgWith("", ""), &awsclient.Clients{}, "us-east-1")
	if source != nil || note != "" {
		t.Fatalf("source %v note %q", source, note)
	}
}

func TestSomethingThatIsNotAnS3UrlIsIgnored(t *testing.T) {
	source, _ := accessLogSource(
		cfgWith("/var/log/alb", ""), &awsclient.Clients{}, "us-east-1",
	)
	if source != nil {
		t.Fatal("a local path was treated as a bucket")
	}
}

// --- flow logs, same problem, harsher consequence ------------------------------

// A flow log prefix that names a region names one region. Reading it while
// collecting another files us-east-1's traffic under eu-west-1, and the
// register then claims every one of those agents runs in a region it has never
// been near — a false statement about the customer's infrastructure rather than
// a missing one.
func TestAFixedFlowLogPrefixRefusesAnotherRegion(t *testing.T) {
	cfg := &config.Config{
		AccountID: "447120043318", Region: "us-east-1",
		FlowLogs: "s3://acme-logs/AWSLogs/1/vpcflowlogs/us-east-1",
		Window:   time.Hour,
	}
	_, err := collectRegion(context.Background(), cfg, "eu-west-1", ingest.Window(time.Hour))
	if err == nil {
		t.Fatal("one region's flow logs were read while collecting another")
	}
	if !strings.Contains(err.Error(), "s3://bucket") {
		t.Fatalf("the error does not say how to fix it: %v", err)
	}
}

func TestABucketAloneIsFineForEveryRegion(t *testing.T) {
	// S3Reader derives AWSLogs/<account>/vpcflowlogs/<region> itself, so there
	// is nothing to refuse. It fails later for want of credentials, which is a
	// different error and not this one.
	cfg := &config.Config{
		AccountID: "447120043318", Region: "us-east-1",
		FlowLogs: "s3://acme-logs", Window: time.Hour,
	}
	_, err := collectRegion(context.Background(), cfg, "eu-west-1", ingest.Window(time.Hour))
	if err != nil && strings.Contains(err.Error(), "cannot be read from it") {
		t.Fatalf("a derivable prefix was refused: %v", err)
	}
}
