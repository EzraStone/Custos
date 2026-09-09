package awsclient

import (
	"context"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws/retry"
)

// A first scan of a large account makes thousands of calls: one
// DescribeNetworkInterfaces page per thousand interfaces, an IAM read per
// principal, a CloudTrail lookup per unresolved address. EC2 throttles, and at
// the SDK's default of three attempts the call fails inside a second.
//
// What the customer sees when it does is not an error. It is a report where
// half the findings are unattributed — the same shape an account with no
// resource tags produces — so the failure arrives disguised as a fact about
// them. That is the reason this is configured rather than left at the default.
func TestTheRetryBudgetIsSizedForABurst(t *testing.T) {
	r, ok := retryer().(*retry.AdaptiveMode)
	if !ok {
		t.Fatalf("adaptive mode is what rate-limits us when AWS pushes back, got %T", retryer())
	}
	if got := r.MaxAttempts(); got != MaxRetryAttempts {
		t.Fatalf("max attempts %d, want %d", got, MaxRetryAttempts)
	}
	if MaxRetryAttempts <= 3 {
		t.Fatal("that is the SDK default, which is what this exists to change")
	}
}

func TestThrottlingIsRetriedAndAClientErrorIsNot(t *testing.T) {
	r := retryer()

	if !r.IsErrorRetryable(&throttled{}) {
		t.Fatal("a throttle must be retried; it is the expected response to a burst")
	}
	if r.IsErrorRetryable(&refused{}) {
		t.Fatal("retrying a permission error wastes the customer's API budget " +
			"and hides a misconfigured role behind a delay")
	}
}

type throttled struct{}

func (throttled) Error() string     { return "RequestLimitExceeded" }
func (throttled) ErrorCode() string { return "RequestLimitExceeded" }

type refused struct{}

func (refused) Error() string     { return "UnauthorizedOperation" }
func (refused) ErrorCode() string { return "UnauthorizedOperation" }

// The credential rules are the ones a security reviewer checks first.
func TestACrossAccountRoleWithoutAnExternalIDIsRefused(t *testing.T) {
	_, err := New(context.Background(), Options{
		Region:  "us-east-1",
		RoleARN: "arn:aws:iam::447120043318:role/custos-discovery",
	})
	if err == nil {
		t.Fatal("a role with no external ID can be assumed by anyone who learns the ARN")
	}
}
