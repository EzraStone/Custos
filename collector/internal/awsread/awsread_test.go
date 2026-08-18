package awsread

import (
	"context"
	"errors"
	"testing"
)

type recordingAPI struct{ called []string }

func (r *recordingAPI) Call(_ context.Context, op string, _ any) (any, error) {
	r.called = append(r.called, op)
	return nil, nil
}

func TestReadOperationsArePermitted(t *testing.T) {
	for _, op := range []string{
		"DescribeNetworkInterfaces", "GetRole", "ListRoleTags",
		"FilterLogEvents", "LookupEvents", "GetLogEvents",
	} {
		if !IsReadOnly(op) {
			t.Errorf("%s should be permitted", op)
		}
	}
}

// TestNoMutatingAPIs enforces SEC-16. If this fails, the collector has grown a
// code path capable of changing something in a customer's account.
func TestNoMutatingAPIs(t *testing.T) {
	mutating := []string{
		"PutObject", "DeleteBucket", "CreateRole", "AttachRolePolicy",
		"TerminateInstances", "UpdateFunctionCode", "RunTask", "ModifyDBInstance",
		"TagResource", "SetLogRetention", "StartQuery",
	}
	api := &recordingAPI{}
	client := New(api, "us-east-1", "1")

	for _, op := range mutating {
		if IsReadOnly(op) {
			t.Errorf("SEC-16: %s must not be classified read-only", op)
		}
		_, err := client.Do(context.Background(), op, nil)
		var notReadOnly ErrNotReadOnly
		if !errors.As(err, &notReadOnly) {
			t.Errorf("SEC-16: %s was not refused, got %v", op, err)
		}
	}

	if len(api.called) != 0 {
		t.Fatalf("SEC-16: refused operations still reached AWS: %v", api.called)
	}
}

// TestStartQueryIsRefused is called out separately because it is the tempting
// one. CloudWatch Logs Insights would be a convenient way to read flow logs,
// StartQuery sounds harmless, and it is a write against the customer's account
// that costs them money.
func TestStartQueryIsRefused(t *testing.T) {
	if IsReadOnly("StartQuery") {
		t.Fatal("SEC-16: StartQuery creates a billable resource and is not read-only")
	}
}

func TestOperationsAreRecordedForTheCustomerAudit(t *testing.T) {
	api := &recordingAPI{}
	client := New(api, "us-east-1", "1")
	ctx := context.Background()
	if _, err := client.Do(ctx, "DescribeNetworkInterfaces", nil); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Do(ctx, "ListRoles", nil); err != nil {
		t.Fatal(err)
	}
	got := client.Operations()
	if len(got) != 2 || got[0] != "DescribeNetworkInterfaces" || got[1] != "ListRoles" {
		t.Fatalf("audit trail wrong: %v", got)
	}
}

func TestWindowValidity(t *testing.T) {
	if (Window{}).Valid() {
		t.Fatal("zero window must be invalid")
	}
}
