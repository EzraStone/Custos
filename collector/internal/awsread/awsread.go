// Package awsread is the collector's only route to AWS, and it can only read.
//
// SEC-16 says the collector holds no write permission and contains no code path
// that mutates customer infrastructure. The IAM policy in deploy/terraform
// enforces the first half. This package enforces the second, so that the claim
// survives a reviewer who assumes the policy might one day be widened by
// accident — defence in depth, in the one place where a mistake is
// unrecoverable in a customer's account.
//
// Every AWS operation the collector performs goes through Client. Operations
// are named explicitly and the set is small enough to read in a minute.
package awsread

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// ReadOnlyVerbs are the AWS API verb prefixes the collector may use. Anything
// else is refused before a request is constructed.
var ReadOnlyVerbs = []string{
	"Describe", "Get", "List", "Filter", "Lookup", "BatchGet", "Search",
}

// ErrNotReadOnly is returned when an operation is not provably read-only.
type ErrNotReadOnly struct{ Operation string }

func (e ErrNotReadOnly) Error() string {
	return fmt.Sprintf(
		"SEC-16: %q is not a read-only operation and the collector may not call it",
		e.Operation,
	)
}

// IsReadOnly reports whether an AWS operation name is provably read-only.
//
// The check is allowlist-based rather than denylist-based on purpose. A
// denylist of destructive verbs would silently permit any verb AWS invents
// after this was written.
func IsReadOnly(operation string) bool {
	for _, verb := range ReadOnlyVerbs {
		if strings.HasPrefix(operation, verb) {
			return true
		}
	}
	return false
}

// API is the narrow surface the collector needs from AWS. Implemented against
// the SDK in production and against a fake in tests, so the collector's logic
// is testable without credentials.
type API interface {
	// Call performs a read-only AWS operation. Implementations must refuse
	// anything IsReadOnly rejects.
	Call(ctx context.Context, operation string, input any) (any, error)
}

// Client wraps an API and refuses non-read operations before they reach it.
type Client struct {
	api      API
	Region   string
	Account  string
	attempts []string
}

func New(api API, region, account string) *Client {
	return &Client{api: api, Region: region, Account: account}
}

// Do performs a read-only operation, refusing anything else.
func (c *Client) Do(ctx context.Context, operation string, input any) (any, error) {
	if !IsReadOnly(operation) {
		return nil, ErrNotReadOnly{Operation: operation}
	}
	c.attempts = append(c.attempts, operation)
	return c.api.Call(ctx, operation, input)
}

// Operations returns every operation this client has attempted. Written to the
// collector's own audit log so a customer can see exactly what was called
// against their account, without taking our word for it.
func (c *Client) Operations() []string {
	out := make([]string, len(c.attempts))
	copy(out, c.attempts)
	return out
}

// Window is an inclusive-exclusive time range to collect over.
type Window struct {
	Start time.Time
	End   time.Time
}

func (w Window) Valid() bool { return w.End.After(w.Start) }
