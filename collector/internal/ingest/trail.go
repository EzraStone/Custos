package ingest

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/cloudtrail"
	trailtypes "github.com/aws/aws-sdk-go-v2/service/cloudtrail/types"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

// CloudTrail is the one attribution path that does not depend on resolving a
// network interface.
//
// When a workload calls Bedrock, CloudTrail records the assumed role and the
// source address in the same event. That maps a principal to an address
// directly: no ENI description to parse, no naming convention to rely on, and
// it works for compute types whose interfaces cannot be resolved at all —
// including EKS pods, which are otherwise stuck at node level.
//
// It is a supplement, not a replacement. CloudTrail sees AWS-native model calls
// and says nothing about traffic to a third-party provider, which is most of
// it. An account calling Anthropic directly gets nothing from this.

// ModelEvents are the event names worth correlating.
//
// Deliberately narrow. LookupEvents is billed per call and returns everything
// by default, so filtering to model invocations keeps the cost proportional to
// what we actually use.
var ModelEvents = []string{
	"InvokeModel",
	"InvokeModelWithResponseStream",
	"Converse",
	"ConverseStream",
	"InvokeAgent",
}

// MaxTrailLookups bounds the whole correlation, not each event name.
//
// A per-name bound multiplies: five event names at ten pages each is fifty
// LookupEvents calls per window, twelve hundred a day at hourly collection,
// against a customer's account for attribution we mostly already have.
//
// A global budget is the right shape because correlation saturates early. We
// need one event per address, not every event, and an account with twenty
// agents is done inside the first page.
const MaxTrailLookups = 12

// TrailCorrelator maps source addresses to the principals CloudTrail saw using
// them.
type TrailCorrelator struct {
	API awsread.TrailAPI
}

// cloudTrailEvent is the subset of an event record we read.
//
// The full record contains request parameters, which for a model invocation can
// include the prompt. Decoding into this struct rather than a map means those
// fields have nowhere to land — the same argument as the wire types, applied to
// the one place a payload could otherwise reach us through an AWS API.
type cloudTrailEvent struct {
	EventTime    time.Time `json:"eventTime"`
	EventName    string    `json:"eventName"`
	SourceIPAddr string    `json:"sourceIPAddress"`
	UserIdentity struct {
		Type       string `json:"type"`
		ARN        string `json:"arn"`
		SessionCtx struct {
			SessionIssuer struct {
				ARN string `json:"arn"`
			} `json:"sessionIssuer"`
		} `json:"sessionContext"`
	} `json:"userIdentity"`
}

// Correlate returns address -> principal ARN for model calls in the window.
//
// A failure returns whatever was gathered alongside the error. Partial
// correlation is still useful, and CloudTrail being unavailable must not cost
// us the interface-based attribution we already have.
func (c *TrailCorrelator) Correlate(
	ctx context.Context, w awsread.Window,
) (map[string]string, error) {
	if c == nil || c.API == nil {
		return nil, nil
	}

	out := map[string]string{}
	var lastErr error
	budget := MaxTrailLookups

	// CloudTrail accepts one lookup attribute per call, so each event name is
	// its own query. Filtering server-side rather than reading everything and
	// discarding most of it keeps the cost proportional to what we use.
	for _, name := range ModelEvents {
		if budget <= 0 {
			break
		}
		spent, err := c.lookup(ctx, w, name, out, budget)
		budget -= spent
		if err != nil {
			lastErr = err
		}
		if err := ctx.Err(); err != nil {
			return out, err
		}
	}

	return out, lastErr
}

// lookup pages one event name, spending at most `budget` calls. Returns how
// many it used.
func (c *TrailCorrelator) lookup(
	ctx context.Context, w awsread.Window, eventName string,
	into map[string]string, budget int,
) (int, error) {
	var token *string
	spent := 0

	for spent < budget {
		spent++
		result, err := c.API.LookupEvents(ctx, &cloudtrail.LookupEventsInput{
			StartTime: aws.Time(w.Start),
			EndTime:   aws.Time(w.End),
			NextToken: token,
			LookupAttributes: []trailtypes.LookupAttribute{{
				AttributeKey:   trailtypes.LookupAttributeKeyEventName,
				AttributeValue: aws.String(eventName),
			}},
		})
		if err != nil {
			return spent, err
		}

		before := len(into)
		for _, event := range result.Events {
			if event.CloudTrailEvent == nil {
				continue
			}
			var decoded cloudTrailEvent
			if err := json.Unmarshal([]byte(*event.CloudTrailEvent), &decoded); err != nil {
				continue
			}
			if address, principal, ok := attribution(decoded); ok {
				// First writer wins. An address serving two principals in one
				// window is real — a task restarting onto a recycled address —
				// and taking the later one would attribute a whole window of
				// traffic to whichever happened to be last.
				if _, seen := into[address]; !seen {
					into[address] = principal
				}
			}
		}

		if result.NextToken == nil || *result.NextToken == "" {
			return spent, nil
		}
		// Stop when a page adds no address we did not already have, whether
		// because correlation saturated or because this event name has
		// nothing in the window. Either way the next page cannot help, and
		// CloudTrail happily hands out continuation tokens for empty results —
		// paging through those spends a customer's budget to learn nothing.
		if len(into) == before {
			return spent, nil
		}
		token = result.NextToken

		if err := ctx.Err(); err != nil {
			return spent, err
		}
	}
	return spent, nil
}

// attribution extracts the address and the role behind an event.
//
// The session issuer is preferred over the identity ARN. An assumed-role event
// carries an ARN like `.../role-name/session-name`, and the session name is per
// task — using it would mint a new principal on every container start and fill
// the register with one-off agents that never appear twice.
func attribution(event cloudTrailEvent) (address, principal string, ok bool) {
	address = strings.TrimSpace(event.SourceIPAddr)
	if address == "" || strings.HasSuffix(address, ".amazonaws.com") {
		// Service-sourced events carry a hostname rather than an address and
		// correlate to nothing.
		return "", "", false
	}

	if issuer := event.UserIdentity.SessionCtx.SessionIssuer.ARN; issuer != "" {
		return address, issuer, true
	}
	if arn := event.UserIdentity.ARN; arn != "" {
		return address, arn, true
	}
	return "", "", false
}
