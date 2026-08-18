package ship

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

func batch() wire.Batch {
	return wire.Batch{
		AccountID: "447120043318", Region: "us-east-1",
		WindowStart: time.Unix(1786370400, 0).UTC(),
		WindowEnd:   time.Unix(1786374000, 0).UTC(),
		Flows: []wire.FlowRecord{{
			AccountID: "447120043318", InterfaceID: "eni-1",
			SrcAddr: "10.0.20.11", DstAddr: "160.79.104.10",
			SrcPort: 43112, DstPort: 443, Protocol: 6,
			Packets: 214, Bytes: 286432, Direction: wire.Egress,
		}},
	}
}

func TestSendPostsJSONWithBearerToken(t *testing.T) {
	var got wire.Batch
	var auth string
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth = r.Header.Get("Authorization")
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.WriteHeader(http.StatusAccepted)
	}))
	defer srv.Close()

	s := New(srv.URL, "secret-token", "test")
	s.client = srv.Client()

	if err := s.Send(context.Background(), batch()); err != nil {
		t.Fatal(err)
	}
	if auth != "Bearer secret-token" {
		t.Fatalf("bad auth header %q", auth)
	}
	if len(got.Flows) != 1 || got.Flows[0].Bytes != 286432 {
		t.Fatalf("batch did not round trip: %+v", got)
	}
	if got.Collector != "test" {
		t.Fatalf("collector version not stamped: %q", got.Collector)
	}
}

// TestClientErrorsAreNotRetried: a rejected batch will be rejected again.
// Retrying a 4xx wastes the customer's egress and hides a config error.
func TestClientErrorsAreNotRetried(t *testing.T) {
	var attempts int
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		attempts++
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()

	s := New(srv.URL, "t", "test")
	s.client = srv.Client()

	err := s.Send(context.Background(), batch())
	if err == nil || !strings.Contains(err.Error(), "401") {
		t.Fatalf("expected a 401 error, got %v", err)
	}
	if attempts != 1 {
		t.Fatalf("4xx must not be retried, saw %d attempts", attempts)
	}
}

func TestCancellationStopsRetrying(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	s := New(srv.URL, "t", "test")
	s.client = srv.Client()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	start := time.Now()
	if err := s.Send(ctx, batch()); err == nil {
		t.Fatal("expected an error")
	}
	if time.Since(start) > 3*time.Second {
		t.Fatal("cancellation did not interrupt the retry backoff")
	}
}

// TestDescribeShowsExactlyWhatWouldBeSent backs the dry-run promise: the
// platform engineer sees the real bytes, not a summary of them.
func TestDescribeShowsExactlyWhatWouldBeSent(t *testing.T) {
	out, err := Describe(batch())
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"286432", "160.79.104.10", "eni-1"} {
		if !strings.Contains(out, want) {
			t.Errorf("dry-run output missing %q", want)
		}
	}
}
