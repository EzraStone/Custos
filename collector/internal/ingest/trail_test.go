package ingest

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/cloudtrail"
	trailtypes "github.com/aws/aws-sdk-go-v2/service/cloudtrail/types"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

type fakeTrail struct {
	events    map[string][]string // event name -> raw event JSON
	calls     int
	names     []string
	err       error
	pageBurst int // pages of the same event before ending
}

func (f *fakeTrail) LookupEvents(_ context.Context, in *cloudtrail.LookupEventsInput,
	_ ...func(*cloudtrail.Options)) (*cloudtrail.LookupEventsOutput, error) {
	f.calls++
	if f.err != nil {
		return nil, f.err
	}

	name := ""
	if len(in.LookupAttributes) > 0 {
		name = aws.ToString(in.LookupAttributes[0].AttributeValue)
	}
	f.names = append(f.names, name)

	out := &cloudtrail.LookupEventsOutput{}
	for _, raw := range f.events[name] {
		out.Events = append(out.Events, trailtypes.Event{CloudTrailEvent: aws.String(raw)})
	}
	if f.pageBurst > 0 {
		f.pageBurst--
		out.NextToken = aws.String("more")
	}
	return out, nil
}

func rawEvent(address, identityARN, issuerARN string) string {
	event := map[string]any{
		"eventTime":       time.Now().UTC().Format(time.RFC3339),
		"eventName":       "InvokeModel",
		"sourceIPAddress": address,
		"userIdentity": map[string]any{
			"type": "AssumedRole",
			"arn":  identityARN,
			"sessionContext": map[string]any{
				"sessionIssuer": map[string]any{"arn": issuerARN},
			},
		},
	}
	raw, _ := json.Marshal(event)
	return string(raw)
}

func recentWindow() awsread.Window {
	end := time.Now().UTC()
	return awsread.Window{Start: end.Add(-time.Hour), End: end}
}

func correlate(t *testing.T, api *fakeTrail) map[string]string {
	t.Helper()
	got, err := (&TrailCorrelator{API: api}).Correlate(context.Background(), recentWindow())
	if err != nil {
		t.Fatal(err)
	}
	return got
}

// The attribution path that works when the interface cannot be resolved at all
// — including EKS pods, otherwise stuck at node level.
func TestAnAddressIsMappedToItsRole(t *testing.T) {
	api := &fakeTrail{events: map[string][]string{
		"InvokeModel": {rawEvent(
			"10.0.20.11",
			"arn:aws:sts::1:assumed-role/finance-close/task-abc123",
			"arn:aws:iam::1:role/finance-close",
		)},
	}}
	got := correlate(t, api)
	if got["10.0.20.11"] != "arn:aws:iam::1:role/finance-close" {
		t.Fatalf("got %v", got)
	}
}

// The session name is per task. Using it would mint a new principal on every
// container start and fill the register with one-off agents.
func TestTheSessionIssuerIsPreferredOverTheSessionARN(t *testing.T) {
	got := correlate(t, &fakeTrail{events: map[string][]string{
		"InvokeModel": {rawEvent(
			"10.0.20.11",
			"arn:aws:sts::1:assumed-role/svc/session-9f2b",
			"arn:aws:iam::1:role/svc",
		)},
	}})
	principal := got["10.0.20.11"]
	if principal != "arn:aws:iam::1:role/svc" {
		t.Fatalf("got %q", principal)
	}
	if len(principal) > 0 && principal[len(principal)-4:] == "9f2b" {
		t.Fatal("the per-task session name leaked into the principal")
	}
}

func TestTheIdentityARNIsUsedWhenThereIsNoIssuer(t *testing.T) {
	got := correlate(t, &fakeTrail{events: map[string][]string{
		"InvokeModel": {rawEvent("10.0.20.11", "arn:aws:iam::1:user/batch", "")},
	}})
	if got["10.0.20.11"] != "arn:aws:iam::1:user/batch" {
		t.Fatalf("got %v", got)
	}
}

// Service-sourced events carry a hostname rather than an address and correlate
// to nothing.
func TestServiceSourcedEventsAreIgnored(t *testing.T) {
	got := correlate(t, &fakeTrail{events: map[string][]string{
		"InvokeModel": {rawEvent("lambda.amazonaws.com", "arn:aws:iam::1:role/x", "")},
	}})
	if len(got) != 0 {
		t.Fatalf("expected nothing, got %v", got)
	}
}

// A task restarting onto a recycled address is real. Taking the later event
// would attribute a whole window of traffic to whichever happened to be last.
func TestTheFirstPrincipalSeenForAnAddressWins(t *testing.T) {
	got := correlate(t, &fakeTrail{events: map[string][]string{
		"InvokeModel": {
			rawEvent("10.0.20.11", "", "arn:aws:iam::1:role/first"),
			rawEvent("10.0.20.11", "", "arn:aws:iam::1:role/second"),
		},
	}})
	if got["10.0.20.11"] != "arn:aws:iam::1:role/first" {
		t.Fatalf("got %v", got)
	}
}

// LookupEvents is billed per call and accepts one attribute, so each event name
// is its own query and the set has to stay small.
func TestEachModelEventNameIsQueriedOnce(t *testing.T) {
	api := &fakeTrail{events: map[string][]string{}}
	correlate(t, api)

	if len(api.names) != len(ModelEvents) {
		t.Fatalf("expected %d queries, got %d", len(ModelEvents), len(api.names))
	}
	for i, name := range ModelEvents {
		if api.names[i] != name {
			t.Fatalf("query %d was %q, want %q", i, api.names[i], name)
		}
	}
}

// A per-name bound multiplies: five event names at ten pages each is fifty
// calls per window against a customer's account, twelve hundred a day at
// hourly collection.
func TestTheLookupBudgetIsGlobalNotPerEventName(t *testing.T) {
	api := &fakeTrail{
		events:    map[string][]string{"InvokeModel": {rawEvent("10.0.0.1", "", "arn:role/x")}},
		pageBurst: 1000,
	}
	correlate(t, api)
	if api.calls > MaxTrailLookups {
		t.Fatalf("made %d calls, budget is %d", api.calls, MaxTrailLookups)
	}
}

// Correlation is a set-cover problem that saturates. Paging past saturation
// spends a customer's budget to learn nothing.
func TestPagingStopsWhenAPageAddsNoNewAddress(t *testing.T) {
	api := &fakeTrail{
		events: map[string][]string{
			"InvokeModel": {rawEvent("10.0.0.1", "", "arn:aws:iam::1:role/x")},
		},
		pageBurst: 1000,
	}
	correlate(t, api)
	// The first page finds the address; the second adds nothing and stops.
	if api.calls > len(ModelEvents)+2 {
		t.Fatalf("kept paging after saturation: %d calls", api.calls)
	}
}

// CloudTrail being unavailable must not cost us the interface-based
// attribution we already have.
func TestAFailureReturnsWhatWasGathered(t *testing.T) {
	api := &fakeTrail{err: errors.New("AccessDeniedException")}
	got, err := (&TrailCorrelator{API: api}).Correlate(context.Background(), recentWindow())
	if err == nil {
		t.Fatal("the failure must be reported")
	}
	if got == nil {
		t.Fatal("a map must still be returned")
	}
}

func TestMalformedEventsAreSkipped(t *testing.T) {
	api := &fakeTrail{events: map[string][]string{
		"InvokeModel": {"{ not json", rawEvent("10.0.0.9", "", "arn:aws:iam::1:role/ok")},
	}}
	got := correlate(t, api)
	if got["10.0.0.9"] != "arn:aws:iam::1:role/ok" {
		t.Fatalf("a malformed event stopped the run: %v", got)
	}
}

func TestANilCorrelatorIsANoOp(t *testing.T) {
	var c *TrailCorrelator
	got, err := c.Correlate(context.Background(), recentWindow())
	if err != nil || got != nil {
		t.Fatalf("got %v, %v", got, err)
	}
}

// The full CloudTrail record contains request parameters, which for a model
// invocation can include the prompt. Decoding into a struct means those fields
// have nowhere to land.
func TestRequestParametersHaveNowhereToLand(t *testing.T) {
	withPrompt := fmt.Sprintf(
		`{"eventTime":%q,"eventName":"InvokeModel","sourceIPAddress":"10.0.0.5",`+
			`"userIdentity":{"arn":"arn:aws:iam::1:role/x"},`+
			`"requestParameters":{"body":"you are a helpful assistant"}}`,
		time.Now().UTC().Format(time.RFC3339))

	var decoded cloudTrailEvent
	if err := json.Unmarshal([]byte(withPrompt), &decoded); err != nil {
		t.Fatal(err)
	}

	raw, err := json.Marshal(decoded)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "helpful assistant") {
		t.Fatalf("prompt text survived decoding: %s", raw)
	}
}
