package config

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/flowlogs"
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

// A dry-run daemon would loop forever printing batches and advancing its
// cursor over windows nothing received.
func TestDaemonAndDryRunAreMutuallyExclusive(t *testing.T) {
	_, err := Load(env(map[string]string{
		"CUSTOS_DAEMON": "1", "CUSTOS_DRY_RUN": "1", "CUSTOS_FLOW_LOGS": "g",
	}))
	if err == nil {
		t.Fatal("a dry-run daemon must be refused")
	}
}

func TestDaemonNeedsSomewhereToShip(t *testing.T) {
	if _, err := Load(env(map[string]string{
		"CUSTOS_DAEMON": "1", "CUSTOS_FLOW_LOGS": "g",
	})); err == nil {
		t.Fatal("a daemon with no endpoint must be refused")
	}
}

func TestStatePathHasADefault(t *testing.T) {
	c, err := Load(env(map[string]string{
		"CUSTOS_ENDPOINT": "https://a.dev", "CUSTOS_TOKEN": "t",
		"CUSTOS_FLOW_LOGS": "g", "CUSTOS_DAEMON": "1",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if c.StatePath == "" {
		t.Fatal("a daemon without a cursor path would never make progress")
	}
}

func TestStatePathIsConfigurable(t *testing.T) {
	c, err := Load(env(map[string]string{
		"CUSTOS_ENDPOINT": "https://a.dev", "CUSTOS_TOKEN": "t",
		"CUSTOS_FLOW_LOGS": "g", "CUSTOS_STATE_PATH": "/var/lib/custos/cursor.json",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if c.StatePath != "/var/lib/custos/cursor.json" {
		t.Fatalf("got %q", c.StatePath)
	}
}

// --- reading a log the customer already has ----------------------------------

func TestNoFormatMeansTheOneWeConfigure(t *testing.T) {
	c := &Config{
		Endpoint: "https://api.custos.dev", Token: "t",
		FlowLogs: "custos-flow-logs", Window: time.Hour,
	}
	f, err := c.Format()
	if err != nil {
		t.Fatal(err)
	}
	if !f.Usable() || f.Count() != flowlogs.Default.Count() {
		t.Fatalf("default format: %v", f.Fields())
	}
}

func TestAnAccountsOwnFormatIsAccepted(t *testing.T) {
	c := &Config{
		Endpoint: "https://api.custos.dev", Token: "t",
		FlowLogs: "existing-flow-logs", Window: time.Hour,
		FlowLogFormat: "${version} ${account-id} ${interface-id} ${srcaddr} " +
			"${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} " +
			"${start} ${end} ${action} ${log-status}",
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	f, err := c.Format()
	if err != nil || !f.Usable() {
		t.Fatalf("format %v err %v", f.Fields(), err)
	}
}

func TestAFormatMissingSomethingRequiredIsRefusedAtStartup(t *testing.T) {
	// Not at the first line read. A bad format produces zero records, and zero
	// records looks exactly like an account with nothing running in it.
	c := &Config{
		Endpoint: "https://api.custos.dev", Token: "t",
		FlowLogs: "existing-flow-logs", Window: time.Hour,
		FlowLogFormat: "${version} ${account-id} ${srcaddr} ${dstaddr}",
	}
	err := c.Validate()
	if err == nil {
		t.Fatal("a format with no byte counts started up")
	}
	if !strings.Contains(err.Error(), "bytes") {
		t.Fatalf("the error does not name what is missing: %v", err)
	}
}

func TestNonsenseInTheFormatVariableIsRefused(t *testing.T) {
	c := &Config{
		Endpoint: "https://api.custos.dev", Token: "t",
		FlowLogs: "g", Window: time.Hour, FlowLogFormat: "SELECT * FROM logs",
	}
	if err := c.Validate(); err == nil {
		t.Fatal("accepted nonsense as a flow log format")
	}
}

func TestTheFormatIsLoadedFromTheEnvironment(t *testing.T) {
	vars := map[string]string{
		"CUSTOS_ENDPOINT":        "https://api.custos.dev",
		"CUSTOS_TOKEN":           "t",
		"CUSTOS_FLOW_LOGS":       "existing",
		"CUSTOS_FLOW_LOG_FORMAT": "interface-id srcaddr dstaddr bytes start end",
	}
	c, err := Load(env(vars))
	if err != nil {
		t.Fatal(err)
	}
	f, err := c.Format()
	if err != nil || f.Count() != 6 {
		t.Fatalf("format %v err %v", f.Fields(), err)
	}
}

// --- more than one region -----------------------------------------------------

func TestOneRegionIsTheDefault(t *testing.T) {
	// An account that runs in one region should not pay for sixteen surveys.
	c := &Config{
		Endpoint: "https://api.custos.dev", Token: "t",
		FlowLogs: "g", Window: time.Hour, Region: "us-east-1",
	}
	if got := c.RegionList(); strings.Join(got, ",") != "us-east-1" {
		t.Fatalf("regions: %v", got)
	}
}

func TestTheCredentialRegionIsAlwaysIncluded(t *testing.T) {
	// It is where the role is assumed and where a single-region account's
	// traffic is. Collecting somewhere else instead of there would be a
	// surprise nobody asked for.
	c := &Config{Region: "us-east-1", Regions: "eu-west-1"}
	if got := strings.Join(c.RegionList(), ","); got != "eu-west-1,us-east-1" {
		t.Fatalf("regions: %v", got)
	}
}

func TestTheListIsSortedAndDeduplicated(t *testing.T) {
	// The order reaches the report, and a region list that reshuffles between
	// scans reads as though something changed.
	c := &Config{Region: "us-east-1", Regions: " eu-west-1, us-east-1 ,ap-south-1, "}
	if got := strings.Join(c.RegionList(), ","); got != "ap-south-1,eu-west-1,us-east-1" {
		t.Fatalf("regions: %v", got)
	}
}

func TestSomethingThatIsNotARegionIsRefused(t *testing.T) {
	// What a mistyped variable actually looks like: a log group path, an ARN,
	// a bucket URL.
	for _, bad := range []string{
		"/aws/vpc/flowlogs", "s3://bucket/prefix",
		"arn:aws:iam::447120043318:role/x", "US-EAST-1",
	} {
		c := &Config{
			Endpoint: "https://api.custos.dev", Token: "t", FlowLogs: "g",
			Window: time.Hour, Region: "us-east-1", Regions: bad,
		}
		if err := c.Validate(); err == nil {
			t.Fatalf("accepted %q as a region", bad)
		}
	}
}

func TestARegionNameWeHaveNeverSeenIsAccepted(t *testing.T) {
	// AWS adds regions. A collector that refused a new one would be wrong in a
	// way nobody could work around.
	c := &Config{
		Endpoint: "https://api.custos.dev", Token: "t", FlowLogs: "g",
		Window: time.Hour, Region: "us-east-1", Regions: "xx-nowhere-1",
	}
	if err := c.Validate(); err != nil {
		t.Fatalf("refused a plausible future region: %v", err)
	}
}

func TestRegionsWithoutACredentialRegionIsRefused(t *testing.T) {
	c := &Config{
		Endpoint: "https://api.custos.dev", Token: "t", FlowLogs: "g",
		Window: time.Hour, Regions: "eu-west-1",
	}
	if err := c.Validate(); err == nil {
		t.Fatal("a region list with nowhere to assume the role was accepted")
	}
}

func TestTheRegionListIsLoadedFromTheEnvironment(t *testing.T) {
	vars := map[string]string{
		"CUSTOS_ENDPOINT":  "https://api.custos.dev",
		"CUSTOS_TOKEN":     "t",
		"CUSTOS_FLOW_LOGS": "g",
		"AWS_REGION":       "us-east-1",
		"CUSTOS_REGIONS":   "eu-west-1,ap-south-1",
	}
	c, err := Load(env(vars))
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(c.RegionList(), ","); got != "ap-south-1,eu-west-1,us-east-1" {
		t.Fatalf("regions: %v", got)
	}
}
