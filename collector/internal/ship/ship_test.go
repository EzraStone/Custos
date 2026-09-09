package ship

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
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
	var auth, encoding string
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth = r.Header.Get("Authorization")
		encoding = r.Header.Get("Content-Encoding")
		zr, err := gzip.NewReader(r.Body)
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		_ = json.NewDecoder(zr).Decode(&got)
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
	if encoding != "gzip" {
		t.Fatalf("body must declare its encoding, got %q", encoding)
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

// --- size ---------------------------------------------------------------------

// One window at the collector's own record limit is 500,000 flow records,
// which is 203MB of JSON. Flow log JSON is the most compressible payload
// imaginable — the same keys, addresses and subnets hundreds of thousands of
// times over — and measures about 32x.
func TestABigBatchGoesOnTheWireSmall(t *testing.T) {
	var big wire.Batch
	big.AccountID = "447120043318"
	for i := 0; i < 20_000; i++ {
		big.Flows = append(big.Flows, wire.FlowRecord{
			AccountID: "447120043318", InterfaceID: fmt.Sprintf("eni-%06x", i%500),
			SrcAddr: "10.0.1.5", DstAddr: "160.79.104.10",
			SrcPort: 41000 + i%20000, DstPort: 443, Protocol: 6,
			Packets: 40, Bytes: 140000, Action: "ACCEPT", LogStatus: "OK",
			VpcID: "vpc-0a1b2c3d", SubnetID: "subnet-0ab12345",
			Direction: wire.Egress, TCPFlags: 19,
		})
	}

	raw, err := json.Marshal(big)
	if err != nil {
		t.Fatal(err)
	}
	sent, err := compress(raw)
	if err != nil {
		t.Fatal(err)
	}
	ratio := float64(len(raw)) / float64(len(sent))
	if ratio < 10 {
		t.Fatalf("compression ratio %.1fx — the size problem is back", ratio)
	}
}

// A fixed thirty-second deadline was a bug waiting for the first busy account:
// the same budget for a 40KB batch and a 200MB one.
func TestTheDeadlineScalesWithWhatIsBeingSent(t *testing.T) {
	small := timeoutFor(40 << 10)
	large := timeoutFor(200 << 20)

	if small < baseTimeout {
		t.Fatalf("a small batch got less than the floor: %v", small)
	}
	if large <= small {
		t.Fatalf("a 200MB body got %v, no more than a 40KB one at %v", large, small)
	}
	if large > maxTimeout {
		t.Fatalf("unbounded: %v", large)
	}
	if timeoutFor(1<<40) != maxTimeout {
		t.Fatal("an absurd body must clamp rather than block a daemon forever")
	}
}
