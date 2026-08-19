package config

import (
	"errors"
	"testing"
)

func env(pairs map[string]string) func(string) string {
	return func(k string) string { return pairs[k] }
}

// TestZeroConfigIsInert enforces SEC-19.
func TestZeroConfigIsInert(t *testing.T) {
	c, err := Load(env(nil))
	if !errors.Is(err, ErrNoEndpoint) {
		t.Fatalf("expected ErrNoEndpoint, got %v", err)
	}
	if c != nil && c.WillSend() {
		t.Fatal("SEC-19: an unconfigured collector must not send")
	}
}

func TestEndpointWithoutTokenIsInert(t *testing.T) {
	_, err := Load(env(map[string]string{
		"CUSTOS_ENDPOINT": "https://api.custos.dev", "CUSTOS_FLOW_LOGS": "/aws/vpc/flowlogs",
	}))
	if !errors.Is(err, ErrNoToken) {
		t.Fatalf("expected ErrNoToken, got %v", err)
	}
}

func TestPlaintextEndpointIsRefused(t *testing.T) {
	_, err := Load(env(map[string]string{
		"CUSTOS_ENDPOINT": "http://api.custos.dev", "CUSTOS_TOKEN": "t",
		"CUSTOS_FLOW_LOGS": "/aws/vpc/flowlogs",
	}))
	if err == nil {
		t.Fatal("plaintext endpoint must be refused")
	}
}

// TestDryRunNeedsNoCredential is how a platform engineer inspects what the
// collector would send before granting it anywhere to send it.
func TestDryRunNeedsNoCredential(t *testing.T) {
	c, err := Load(env(map[string]string{
		"CUSTOS_DRY_RUN": "1", "CUSTOS_FLOW_LOGS": "/aws/vpc/flowlogs",
	}))
	if err != nil {
		t.Fatalf("dry run should validate without credentials: %v", err)
	}
	if c.WillSend() {
		t.Fatal("dry run must never send")
	}
}

func TestFlowLogSourceIsRequiredEvenInDryRun(t *testing.T) {
	if _, err := Load(env(map[string]string{"CUSTOS_DRY_RUN": "1"})); err == nil {
		t.Fatal("a source is required; there is nothing to read without one")
	}
}

func TestValidConfigWillSend(t *testing.T) {
	c, err := Load(env(map[string]string{
		"CUSTOS_ENDPOINT": "https://api.custos.dev", "CUSTOS_TOKEN": "t",
		"CUSTOS_FLOW_LOGS": "/aws/vpc/flowlogs", "CUSTOS_WINDOW": "30m",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if !c.WillSend() || c.Window.Minutes() != 30 {
		t.Fatalf("unexpected config %+v", c)
	}
}

func TestBadWindowIsRejected(t *testing.T) {
	_, err := Load(env(map[string]string{
		"CUSTOS_ENDPOINT": "https://a.dev", "CUSTOS_TOKEN": "t",
		"CUSTOS_FLOW_LOGS": "g", "CUSTOS_WINDOW": "not-a-duration",
	}))
	if err == nil {
		t.Fatal("unparseable window must be rejected")
	}
}

func TestCrossAccountRoleRequiresAnExternalID(t *testing.T) {
	_, err := Load(env(map[string]string{
		"CUSTOS_DRY_RUN": "1", "CUSTOS_FLOW_LOGS": "g",
		"CUSTOS_ROLE_ARN": "arn:aws:iam::1:role/custos-discovery",
	}))
	if err == nil {
		t.Fatal("a cross-account role without an external ID must be refused")
	}
}

func TestS3SourceIsDetected(t *testing.T) {
	c := &Config{FlowLogs: "s3://acme-flow-logs/AWSLogs/1/vpcflowlogs/us-east-1"}
	bucket, prefix, ok := c.S3Source()
	if !ok || bucket != "acme-flow-logs" || prefix != "AWSLogs/1/vpcflowlogs/us-east-1" {
		t.Fatalf("got %q %q %v", bucket, prefix, ok)
	}
}

func TestCloudWatchGroupIsNotMistakenForS3(t *testing.T) {
	c := &Config{FlowLogs: "/aws/vpc/flowlogs"}
	if _, _, ok := c.S3Source(); ok {
		t.Fatal("a log group name must not parse as an S3 source")
	}
}

func TestBucketWithNoPrefixIsValid(t *testing.T) {
	c := &Config{FlowLogs: "s3://acme-flow-logs"}
	bucket, prefix, ok := c.S3Source()
	if !ok || bucket != "acme-flow-logs" || prefix != "" {
		t.Fatalf("got %q %q %v", bucket, prefix, ok)
	}
}
