// Command custos-collector reads network and identity metadata from an AWS
// account and ships it to the Custos control plane.
//
// It runs under a cross-account role with read-only permissions. It installs
// nothing, mutates nothing, and never reads a payload byte.
//
// Run it with no configuration and it exits having done nothing (SEC-19). Run
// it with CUSTOS_DRY_RUN=1 and it prints exactly what it would send, which is
// the recommended first step in any security review.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsclient"
	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/config"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
	"github.com/EzraStone/Custos/collector/internal/ingest"
	"github.com/EzraStone/Custos/collector/internal/schedule"
	"github.com/EzraStone/Custos/collector/internal/ship"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// Version is stamped at build time with -ldflags.
var Version = "dev"

func main() {
	if err := run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "custos-collector:", err)
		os.Exit(1)
	}
}

func run(args []string, stdout, stderr *os.File) error {
	fs := flag.NewFlagSet("custos-collector", flag.ContinueOnError)
	fs.SetOutput(stderr)
	showVersion := fs.Bool("version", false, "print version and exit")
	explain := fs.Bool("explain", false, "print what this binary reads and sends, then exit")
	input := fs.String("from-file", "", "read flow log lines from a file instead of AWS")
	if err := fs.Parse(args); err != nil {
		return err
	}

	switch {
	case *showVersion:
		fmt.Fprintln(stdout, Version)
		return nil
	case *explain:
		fmt.Fprint(stdout, explanation)
		return nil
	}

	cfg, err := config.FromEnv()
	if err != nil {
		// SEC-19 refusals are the expected outcome of running this by accident,
		// so they exit cleanly with an explanation rather than a stack trace.
		if errors.Is(err, config.ErrNoEndpoint) || errors.Is(err, config.ErrNoToken) {
			fmt.Fprintln(stderr, err)
			fmt.Fprintln(stderr, "\nRun with --explain to see what this binary does.")
			return nil
		}
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if cfg.Daemon {
		return serve(ctx, cfg, stdout, stderr)
	}
	return collect(ctx, cfg, *input, stdout, stderr)
}

// serve collects on a schedule until interrupted.
//
// Every window goes through the same path a one-shot run does, so there is no
// second code path to keep correct — the only difference is who decides the
// window and what happens to the cursor afterwards.
func serve(ctx context.Context, cfg *config.Config, stdout, stderr *os.File) error {
	fmt.Fprintf(stderr, "collecting every %s, cursor at %s\n", cfg.Window, cfg.StatePath)

	return schedule.Run(ctx, schedule.Options{
		Interval: cfg.Window,
		State:    schedule.Store{Path: cfg.StatePath},
		Log:      stderr,
	}, func(ctx context.Context, w awsread.Window) error {
		batch, report, err := fromAWSWindow(ctx, cfg, w)
		if err != nil {
			return err
		}
		batch.Collector = Version

		fmt.Fprint(stderr, report.Summary())
		return ship.New(cfg.Endpoint, cfg.Token, Version).Send(ctx, batch)
	})
}

func collect(ctx context.Context, cfg *config.Config, path string, stdout, stderr *os.File) error {
	batch, report, err := build(ctx, cfg, path)
	if err != nil {
		return err
	}
	// Stamped here rather than only in the shipper, so a dry run shows the
	// same bytes that would actually be sent.
	batch.Collector = Version

	fmt.Fprint(stderr, report.Summary())
	if !report.Trustworthy() {
		// Said plainly rather than buried. A scan with poor coverage that finds
		// nothing is not the same as a clean account, and the difference is the
		// whole meaning of the result.
		fmt.Fprintln(stderr,
			"NOTE: coverage was incomplete — an absence of findings means less than usual")
	}

	if !cfg.WillSend() {
		out, err := ship.Describe(batch)
		if err != nil {
			return err
		}
		fmt.Fprintln(stdout, out)
		fmt.Fprintln(stderr, "dry run: nothing was sent")
		return nil
	}

	return ship.New(cfg.Endpoint, cfg.Token, Version).Send(ctx, batch)
}

// build assembles a batch, from a local file when one is given and from AWS
// otherwise. The file path exists so a customer can hand us an export without
// granting any access at all, which is a useful first step in a review.
func build(ctx context.Context, cfg *config.Config, path string) (wire.Batch, ingest.Report, error) {
	if path != "" {
		return fromFile(cfg, path)
	}
	return fromAWS(ctx, cfg)
}

func fromFile(cfg *config.Config, path string) (wire.Batch, ingest.Report, error) {
	fh, err := os.Open(path)
	if err != nil {
		return wire.Batch{}, ingest.Report{}, err
	}
	defer fh.Close()

	records, stats, err := flowlogs.Parse(fh)
	if err != nil {
		return wire.Batch{}, ingest.Report{}, fmt.Errorf("parsing flow logs: %w", err)
	}

	end := time.Now().UTC()
	return wire.Batch{
		AccountID:   cfg.AccountID,
		Region:      cfg.Region,
		WindowStart: end.Add(-cfg.Window),
		WindowEnd:   end,
		Flows:       records,
	}, ingest.Report{Stats: stats, Interfaces: len(ingest.DistinctInterfaces(records))}, nil
}

func fromAWS(ctx context.Context, cfg *config.Config) (wire.Batch, ingest.Report, error) {
	return fromAWSWindow(ctx, cfg, ingest.Window(cfg.Window))
}

func fromAWSWindow(
	ctx context.Context, cfg *config.Config, w awsread.Window,
) (wire.Batch, ingest.Report, error) {
	clients, err := awsclient.New(ctx, awsclient.Options{
		Region:     cfg.Region,
		RoleARN:    cfg.RoleARN,
		ExternalID: cfg.ExternalID,
	})
	if err != nil {
		return wire.Batch{}, ingest.Report{}, err
	}

	var source ingest.FlowSource
	if bucket, prefix, ok := cfg.S3Source(); ok {
		source = &ingest.S3Reader{
			API: clients.Objects, Bucket: bucket, Prefix: prefix,
			AccountID: cfg.AccountID, Region: cfg.Region,
		}
	} else {
		source = &ingest.CloudWatchReader{API: clients.Logs, Group: cfg.FlowLogs}
	}

	collector := &ingest.Collector{
		Flows:      source,
		Requests:   accessLogSource(cfg, clients),
		Network:    clients.Network,
		Identity:   clients.Identity,
		Serverless: clients.Serverless,
		AccountID:  cfg.AccountID,
		Region:     cfg.Region,
	}
	return collector.Collect(ctx, w)
}

// accessLogSource returns a reader for load balancer access logs, or nil when
// the customer has not pointed us at any. Nil is a supported state, not a
// failure: the classifier reports reduced recall rather than guessing.
func accessLogSource(cfg *config.Config, clients *awsclient.Clients) ingest.RequestSource {
	if cfg.AccessLogs == "" {
		return nil
	}
	rest, found := strings.CutPrefix(cfg.AccessLogs, "s3://")
	if !found {
		return nil
	}
	bucket, prefix, _ := strings.Cut(rest, "/")
	if bucket == "" {
		return nil
	}
	return &ingest.AccessLogReader{API: clients.Objects, Bucket: bucket, Prefix: prefix}
}

const explanation = `custos-collector

WHAT IT READS
  VPC Flow Logs        network metadata: addresses, ports, byte counts, timings
  CloudTrail           which principal is attached to which network interface
  IAM (read-only)      role tags, IAM paths, and attached policy actions
  ALB access logs      optional: when a request arrived and how large it was

WHAT IT SENDS
  Exactly the structures in internal/wire. There is no field on any of them
  capable of holding a prompt, a completion, or any other payload body, and the
  shipper accepts nothing else. Run with CUSTOS_DRY_RUN=1 to print the literal
  bytes before granting it anywhere to send them.

WHAT IT CANNOT DO
  Write anything. Every AWS call goes through internal/awsread, which refuses
  any operation whose verb is not Describe, Get, List, Filter, Lookup, BatchGet,
  or Search — before a request is constructed. The IAM policy in
  deploy/terraform grants no write permission either.

CONFIGURATION
  CUSTOS_ENDPOINT      https URL of the control plane   (required to send)
  CUSTOS_TOKEN         credential you hold              (required to send)
  CUSTOS_FLOW_LOGS     log group name, or s3://bucket/prefix   (required)
  CUSTOS_ACCESS_LOGS   s3://bucket/prefix for ALB logs  (optional, lifts recall
                       from 60% to 100% on our test corpus)
  CUSTOS_ACCOUNT_ID    account being scanned
  CUSTOS_WINDOW        collection window, default 1h
  CUSTOS_ROLE_ARN      cross-account role to assume     (optional)
  CUSTOS_EXTERNAL_ID   required whenever a role is assumed
  CUSTOS_DAEMON=1      collect on a schedule instead of once
  CUSTOS_STATE_PATH    where the collection cursor lives across restarts
  CUSTOS_DRY_RUN=1     read and print, never send

With no endpoint or token configured this binary does nothing at all.
`
