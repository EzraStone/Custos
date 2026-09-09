// Package ship sends a batch to the control plane.
//
// The signature of Send is the load-bearing part of this package:
//
//	func (s *Shipper) Send(ctx context.Context, batch wire.Batch) error
//
// It takes a wire.Batch and nothing else. There is no io.Reader parameter, no
// []byte parameter, and no generic payload. Combined with the field allowlist
// in package wire, that is the complete proof of SEC-18: a reviewer can confirm
// that no payload byte can leave the account by reading two files.
package ship

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

const (
	userAgent   = "custos-collector"
	maxAttempts = 4

	// baseTimeout covers connection setup and the control plane's own work on
	// a small batch. It is a floor, not the budget: see timeoutFor.
	baseTimeout = 30 * time.Second

	// perMegabyte is added to the timeout for every megabyte on the wire,
	// which is roughly 8 Mbit/s sustained. Deliberately pessimistic: a
	// collector running inside a customer's VPC behind an egress proxy has no
	// claim on the bandwidth a laptop would see.
	perMegabyte = time.Second

	// maxTimeout bounds the whole thing. A send that has not completed in ten
	// minutes is not going to, and a daemon blocked on one is a daemon that
	// has stopped collecting.
	maxTimeout = 10 * time.Minute
)

// timeoutFor scales the request deadline to the size of what is being sent.
//
// A fixed thirty seconds was a bug waiting for the first busy account. One
// window at the collector's own record limit is 500,000 flow records, which is
// 203MB of JSON — 6.2MB gzipped, but still far more than thirty seconds of a
// throttled egress path. The failure it produces is the worst kind: the
// collector reports that shipping failed, retries three times, and the
// customer's first impression is that the product does not work.
func timeoutFor(bodyBytes int) time.Duration {
	d := baseTimeout + time.Duration(bodyBytes/(1<<20))*perMegabyte
	if d > maxTimeout {
		return maxTimeout
	}
	return d
}

// Shipper posts batches over TLS.
type Shipper struct {
	endpoint string
	token    string
	client   *http.Client
	version  string
}

// New returns a Shipper. TLS 1.2 is the floor; a customer's egress proxy that
// cannot do 1.2 is a finding in its own right.
func New(endpoint, token, version string) *Shipper {
	return &Shipper{
		endpoint: endpoint,
		token:    token,
		version:  version,
		client: &http.Client{
			// No client-level timeout: it would apply the same deadline to a
			// 40KB batch and a 200MB one. The deadline is set per request,
			// from the size of the body.
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12},
			},
		},
	}
}

// Send transmits one batch. Retries are bounded and idempotent: the control
// plane deduplicates on (account, window), so a retried batch cannot double
// count an agent's traffic.
func (s *Shipper) Send(ctx context.Context, batch wire.Batch) error {
	batch.Collector = s.version

	raw, err := json.Marshal(batch)
	if err != nil {
		return fmt.Errorf("encoding batch: %w", err)
	}

	// Compressed, because flow log JSON is the most compressible payload
	// imaginable: the same keys, the same addresses and the same subnets
	// repeated hundreds of thousands of times. Measured at 32x on a full
	// window — 203MB becomes 6.2MB — which is the difference between a send
	// that completes over a customer's egress path and one that does not.
	body, err := compress(raw)
	if err != nil {
		return fmt.Errorf("compressing batch: %w", err)
	}

	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		if attempt > 1 {
			delay := time.Duration(1<<(attempt-2)) * 2 * time.Second
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(delay):
			}
		}

		status, err := s.post(ctx, body)
		switch {
		case err == nil && status < 300:
			return nil
		case err == nil && status >= 400 && status < 500:
			// A rejected batch will be rejected again. Retrying a 4xx wastes
			// the customer's egress and hides a configuration error.
			return fmt.Errorf("control plane rejected batch: HTTP %d", status)
		case err == nil:
			lastErr = fmt.Errorf("control plane error: HTTP %d", status)
		default:
			lastErr = err
		}
	}
	return fmt.Errorf("after %d attempts: %w", maxAttempts, lastErr)
}

func compress(raw []byte) ([]byte, error) {
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	if _, err := zw.Write(raw); err != nil {
		return nil, err
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func (s *Shipper) post(ctx context.Context, body []byte) (int, error) {
	ctx, cancel := context.WithTimeout(ctx, timeoutFor(len(body)))
	defer cancel()

	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost, s.endpoint+"/v1/batches", bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Content-Encoding", "gzip")
	req.Header.Set("Authorization", "Bearer "+s.token)
	req.Header.Set("User-Agent", userAgent+"/"+s.version)

	resp, err := s.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	return resp.StatusCode, nil
}

// Describe renders what would be sent, for dry-run mode. This is what a
// platform engineer reads before deciding whether to grant an endpoint.
func Describe(batch wire.Batch) (string, error) {
	out, err := json.MarshalIndent(batch, "", "  ")
	if err != nil {
		return "", err
	}
	return string(out), nil
}
