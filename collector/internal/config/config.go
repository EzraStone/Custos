// Package config loads the collector's configuration and refuses to do
// anything without it.
//
// SEC-19: the collector performs no reads and no network egress until it has
// been given a control plane endpoint and a customer-held credential. A
// collector binary run with no configuration reads nothing and sends nothing.
//
// That invariant exists for one conversation. In a security review, "what
// happens if someone runs this by accident, or if it ends up on the wrong host"
// needs a boring answer, and "it exits immediately having done nothing" is the
// most boring answer available.
package config

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
	"time"
)

// Config is everything the collector needs. There are no defaults for the two
// fields that matter.
type Config struct {
	// Endpoint is the Custos control plane. No default, deliberately.
	Endpoint string

	// Token authenticates this collector. Held by the customer, never embedded.
	Token string

	AccountID  string
	Region     string
	FlowLogs   string // CloudWatch Logs group name, or s3://bucket/prefix
	AccessLogs string // optional: ALB access log prefix
	Window     time.Duration
	DryRun     bool // read and print, never send

	// RoleARN is the cross-account role to assume. Empty means use ambient
	// credentials, which is how a customer runs this inside their own account.
	RoleARN string

	// ExternalID guards the confused deputy problem. Required with RoleARN.
	ExternalID string

	// Daemon runs collection on a schedule instead of once.
	Daemon bool

	// StatePath holds the collection cursor across restarts. Without a durable
	// path a restarting daemon re-collects one window and loses nothing, but a
	// daemon that restarts often would never make progress past its interval.
	StatePath string
}

// S3Source reports whether FlowLogs names an S3 location, returning the bucket
// and prefix. Otherwise FlowLogs is a CloudWatch Logs group name.
func (c *Config) S3Source() (bucket, prefix string, ok bool) {
	rest, found := strings.CutPrefix(c.FlowLogs, "s3://")
	if !found {
		return "", "", false
	}
	bucket, prefix, _ = strings.Cut(rest, "/")
	return bucket, prefix, bucket != ""
}

var (
	// ErrNoEndpoint and ErrNoToken are the two halves of SEC-19.
	ErrNoEndpoint = errors.New(
		"SEC-19: no control plane endpoint configured; the collector does nothing")
	ErrNoToken = errors.New(
		"SEC-19: no credential configured; the collector does nothing")
)

const DefaultWindow = time.Hour

// Load reads configuration from the environment.
func Load(getenv func(string) string) (*Config, error) {
	c := &Config{
		Endpoint:   strings.TrimSpace(getenv("CUSTOS_ENDPOINT")),
		Token:      strings.TrimSpace(getenv("CUSTOS_TOKEN")),
		AccountID:  strings.TrimSpace(getenv("CUSTOS_ACCOUNT_ID")),
		Region:     strings.TrimSpace(getenv("AWS_REGION")),
		FlowLogs:   strings.TrimSpace(getenv("CUSTOS_FLOW_LOGS")),
		AccessLogs: strings.TrimSpace(getenv("CUSTOS_ACCESS_LOGS")),
		Window:     DefaultWindow,
		DryRun:     getenv("CUSTOS_DRY_RUN") == "1",
		RoleARN:    strings.TrimSpace(getenv("CUSTOS_ROLE_ARN")),
		ExternalID: strings.TrimSpace(getenv("CUSTOS_EXTERNAL_ID")),
		Daemon:     getenv("CUSTOS_DAEMON") == "1",
		StatePath:  strings.TrimSpace(getenv("CUSTOS_STATE_PATH")),
	}
	if c.StatePath == "" {
		c.StatePath = "custos-collector-state.json"
	}
	if raw := getenv("CUSTOS_WINDOW"); raw != "" {
		d, err := time.ParseDuration(raw)
		if err != nil {
			return nil, fmt.Errorf("CUSTOS_WINDOW: %w", err)
		}
		c.Window = d
	}
	return c, c.Validate()
}

// FromEnv loads from the real process environment.
func FromEnv() (*Config, error) { return Load(os.Getenv) }

// Validate enforces SEC-19 and the minimum needed to read anything.
//
// Dry run is exempt from the endpoint and token requirements and only from
// those: it is how a platform engineer sees what the collector would send
// before granting it anywhere to send it, which is the single most effective
// thing we can offer in a security review.
func (c *Config) Validate() error {
	if !c.DryRun {
		if c.Endpoint == "" {
			return ErrNoEndpoint
		}
		if c.Token == "" {
			return ErrNoToken
		}
		u, err := url.Parse(c.Endpoint)
		if err != nil || u.Scheme != "https" {
			return fmt.Errorf("CUSTOS_ENDPOINT must be an https URL, got %q", c.Endpoint)
		}
	}
	if c.FlowLogs == "" {
		return errors.New("CUSTOS_FLOW_LOGS is required: nothing to read without it")
	}
	if c.Window <= 0 {
		return errors.New("CUSTOS_WINDOW must be positive")
	}
	if c.RoleARN != "" && c.ExternalID == "" {
		// A cross-account role with no external ID can be assumed by anyone
		// who learns the ARN. Refusing here is cheaper than explaining it in a
		// post-mortem.
		return errors.New(
			"CUSTOS_EXTERNAL_ID is required when CUSTOS_ROLE_ARN is set")
	}
	if c.RoleARN != "" && c.Region == "" {
		return errors.New("AWS_REGION is required when assuming a role")
	}
	if c.Daemon && c.DryRun {
		// A dry-run daemon would loop forever printing batches and advancing
		// its cursor over windows nothing received. Refusing is clearer than
		// letting someone discover it a day later.
		return errors.New("CUSTOS_DAEMON and CUSTOS_DRY_RUN are mutually exclusive")
	}
	return nil
}

// WillSend reports whether this configuration permits network egress at all.
func (c *Config) WillSend() bool {
	return !c.DryRun && c.Endpoint != "" && c.Token != ""
}
