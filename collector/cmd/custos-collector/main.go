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
	"syscall"
	"time"

	"github.com/EzraStone/Custos/collector/internal/config"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
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

	return collect(ctx, cfg, *input, stdout, stderr)
}

func collect(ctx context.Context, cfg *config.Config, path string, stdout, stderr *os.File) error {
	if path == "" {
		return errors.New(
			"AWS log ingestion is not wired up yet; use --from-file with exported flow logs")
	}

	fh, err := os.Open(path)
	if err != nil {
		return err
	}
	defer fh.Close()

	records, stats, err := flowlogs.Parse(fh)
	if err != nil {
		return fmt.Errorf("parsing flow logs: %w", err)
	}

	fmt.Fprintf(stderr, "parsed %d of %d lines (%.1f%% coverage)",
		stats.Parsed, stats.Lines, stats.Coverage()*100)
	if stats.SkipData > 0 {
		// Surfaced loudly. High SKIPDATA means the account's traffic is
		// under-represented and an absence of findings means less.
		fmt.Fprintf(stderr, "; %d SKIPDATA lines — traffic is under-represented", stats.SkipData)
	}
	fmt.Fprintln(stderr)

	batch := wire.Batch{
		AccountID:   cfg.AccountID,
		Region:      cfg.Region,
		WindowStart: time.Now().UTC().Add(-cfg.Window),
		WindowEnd:   time.Now().UTC(),
		Flows:       records,
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
  CUSTOS_FLOW_LOGS     log group or S3 prefix           (required)
  CUSTOS_ACCESS_LOGS   ALB access log prefix            (optional, improves recall)
  CUSTOS_ACCOUNT_ID    account being scanned
  CUSTOS_WINDOW        collection window, default 1h
  CUSTOS_DRY_RUN=1     read and print, never send

With no endpoint or token configured this binary does nothing at all.
`
