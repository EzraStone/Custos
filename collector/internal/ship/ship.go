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
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

const (
	userAgent      = "custos-collector"
	defaultTimeout = 30 * time.Second
	maxAttempts    = 4
)

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
			Timeout: defaultTimeout,
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

	body, err := json.Marshal(batch)
	if err != nil {
		return fmt.Errorf("encoding batch: %w", err)
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

func (s *Shipper) post(ctx context.Context, body []byte) (int, error) {
	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost, s.endpoint+"/v1/batches", bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
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
