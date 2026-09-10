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
	"github.com/EzraStone/Custos/collector/internal/preflight"
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
	check := fs.Bool("check", false, "run preflight checks and exit")
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

	if *check {
		return preflightCheck(ctx, cfg, stdout)
	}

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
		collections, err := fromAWSWindow(ctx, cfg, w)
		if err != nil {
			return err
		}

		shipper := ship.New(cfg.Endpoint, cfg.Token, Version)
		for _, c := range collections {
			c.Batch.Collector = Version
			if len(collections) > 1 {
				fmt.Fprintf(stderr, "\n%s\n", c.Region)
			}
			fmt.Fprint(stderr, c.Report.Summary())
			// Any region failing to ship holds the cursor, so the whole window
			// is retried. Advancing past a region whose batch never arrived
			// would lose it silently, which is the one thing the cursor exists
			// to prevent.
			if err := shipper.Send(ctx, c.Batch); err != nil {
				return err
			}
		}
		return nil
	})
}

func collect(ctx context.Context, cfg *config.Config, path string, stdout, stderr *os.File) error {
	collections, err := build(ctx, cfg, path)
	if err != nil {
		return err
	}

	shipper := ship.New(cfg.Endpoint, cfg.Token, Version)
	for _, c := range collections {
		// Stamped here rather than only in the shipper, so a dry run shows the
		// same bytes that would actually be sent.
		c.Batch.Collector = Version

		if len(collections) > 1 {
			fmt.Fprintf(stderr, "\n%s\n", c.Region)
		}
		fmt.Fprint(stderr, c.Report.Summary())
		if !c.Report.Trustworthy() {
			// Said plainly rather than buried. A scan with poor coverage that
			// finds nothing is not the same as a clean account, and the
			// difference is the whole meaning of the result.
			fmt.Fprintln(stderr,
				"NOTE: coverage was incomplete — an absence of findings means less than usual")
		}

		if !cfg.WillSend() {
			out, err := ship.Describe(c.Batch)
			if err != nil {
				return err
			}
			fmt.Fprintln(stdout, out)
			continue
		}
		if err := shipper.Send(ctx, c.Batch); err != nil {
			return err
		}
	}

	if !cfg.WillSend() {
		fmt.Fprintln(stderr, "dry run: nothing was sent")
	}
	return nil
}

// build assembles a batch, from a local file when one is given and from AWS
// otherwise. The file path exists so a customer can hand us an export without
// granting any access at all, which is a useful first step in a review.
func build(ctx context.Context, cfg *config.Config, path string) ([]Collection, error) {
	if path != "" {
		return fromFile(cfg, path)
	}
	return fromAWS(ctx, cfg)
}

func fromFile(cfg *config.Config, path string) ([]Collection, error) {
	fh, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer fh.Close()

	format, err := cfg.Format()
	if err != nil {
		return nil, err
	}
	records, stats, err := flowlogs.ParseFormatted(fh, format)
	if err != nil {
		return nil, fmt.Errorf("parsing flow logs: %w", err)
	}

	// No attribution on this path, so direction inference has only the records
	// themselves to work from. It still answers for any interface that talked
	// to more than one peer, which on a real file is nearly all of them.
	interfaces := len(ingest.DistinctInterfaces(records))
	records, direction := flowlogs.InferDirection(records, nil)

	end := time.Now().UTC()
	batch := wire.Batch{
		AccountID:   cfg.AccountID,
		Region:      cfg.Region,
		WindowStart: end.Add(-cfg.Window),
		WindowEnd:   end,
		Flows:       records,
		Collection: wire.Collection{
			LinesRead:          int64(stats.Lines),
			LinesParsed:        int64(stats.Parsed),
			LinesMalformed:     int64(stats.Malformed),
			RecordsSkipped:     int64(stats.SkipData + stats.NoData),
			Truncated:          stats.Truncated,
			MissingFields:      format.Absent(),
			DirectionInferred:  int64(direction.Inferred),
			DirectionUndecided: int64(direction.Undecided),
		},
	}
	return []Collection{{
		Region: cfg.Region,
		Batch:  batch,
		Report: ingest.Report{Stats: stats, Interfaces: interfaces, Direction: direction},
	}}, nil
}

// Collection is one region's batch and the report on collecting it.
type Collection struct {
	Region string
	Batch  wire.Batch
	Report ingest.Report
}

func fromAWS(ctx context.Context, cfg *config.Config) ([]Collection, error) {
	return fromAWSWindow(ctx, cfg, ingest.Window(cfg.Window))
}

// fromAWSWindow collects every configured region for one window.
//
// One batch per region rather than one merged batch. A private address is
// unique within a region and nowhere else, so a batch holding two regions'
// traffic would key destination names and gateway questions on addresses that
// mean two different things — and the control plane keys a batch on
// (account, region, window) for exactly that reason.
//
// A region that fails does not take the others with it. A role that cannot
// read eu-west-1 is a reason to say so about eu-west-1, not a reason to lose
// us-east-1 as well.
func fromAWSWindow(
	ctx context.Context, cfg *config.Config, w awsread.Window,
) ([]Collection, error) {
	regions := cfg.RegionList()
	var (
		out      []Collection
		failures []string
	)
	for _, region := range regions {
		collected, err := collectRegion(ctx, cfg, region, w)
		if err != nil {
			failures = append(failures, fmt.Sprintf("%s: %v", region, err))
			continue
		}
		out = append(out, collected)
	}

	if len(out) == 0 {
		return nil, fmt.Errorf("no region could be collected: %s",
			strings.Join(failures, "; "))
	}
	for i := range out {
		// Carried on every collection so the caller can print them once
		// without threading a second return value through the daemon loop.
		out[i].Report.Errors = append(out[i].Report.Errors, failures...)
		failures = nil
	}
	return out, nil
}

func collectRegion(
	ctx context.Context, cfg *config.Config, region string, w awsread.Window,
) (Collection, error) {
	clients, err := awsclient.New(ctx, awsclient.Options{
		Region:     region,
		RoleARN:    cfg.RoleARN,
		ExternalID: cfg.ExternalID,
	})
	if err != nil {
		return Collection{}, err
	}

	format, err := cfg.Format()
	if err != nil {
		return Collection{}, err
	}

	var source ingest.FlowSource
	if bucket, prefix, ok := cfg.S3Source(); ok {
		source = &ingest.S3Reader{
			API: clients.Objects, Bucket: bucket, Prefix: prefix,
			AccountID: cfg.AccountID, Region: region, Format: format,
		}
	} else {
		source = &ingest.CloudWatchReader{
			API: clients.Logs, Group: cfg.FlowLogs, Format: format,
		}
	}

	collector := &ingest.Collector{
		Format:     format,
		Flows:      source,
		Requests:   accessLogSource(cfg, clients),
		Network:    clients.Network,
		Identity:   clients.Identity,
		Serverless: clients.Serverless,
		Trail:      clients.Trail,
		AccountID:  cfg.AccountID,
		Region:     region,
	}
	batch, report, err := collector.Collect(ctx, w)
	return Collection{Region: region, Batch: batch, Report: report}, err
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

// preflightCheck answers "why did the scan find nothing" before the scan.
//
// Run before every first scan. Every onboarding failure produces the same
// symptom — a report with no findings — and the only way to tell that apart
// from a clean account is to check before rather than infer after.
func preflightCheck(ctx context.Context, cfg *config.Config, stdout *os.File) error {
	pf := preflight.Config{
		AccountID:    cfg.AccountID,
		Region:       cfg.Region,
		RoleARN:      cfg.RoleARN,
		ExternalID:   cfg.ExternalID,
		FlowLogs:     cfg.FlowLogs,
		AccessLogs:   cfg.AccessLogs,
		HaveEndpoint: cfg.Endpoint != "",
		HaveToken:    cfg.Token != "",
	}
	// Reported even when credentials are absent: the format is the one thing
	// preflight can check about the log without being able to read it.
	if format, err := cfg.Format(); err == nil {
		pf.Format = format
	}

	var (
		source  preflight.FlowSource
		namer   preflight.Namer
		regions preflight.Regions
	)
	if clients, err := awsclient.New(ctx, awsclient.Options{
		Region: cfg.Region, RoleARN: cfg.RoleARN, ExternalID: cfg.ExternalID,
	}); err == nil {
		namer = &ingest.DestinationResolver{API: clients.Network}
		// The one place the collector looks outside the configured region, and
		// it does one read per region. Without it --check cannot say whether
		// this scan covers the account or a sixteenth of it.
		regions = &ingest.Survey{Regions: clients.Network, FlowLogsIn: clients.FlowLogsIn}
		format, err := cfg.Format()
		if err != nil {
			return fmt.Errorf("flow log format: %w", err)
		}
		if bucket, prefix, ok := cfg.S3Source(); ok {
			source = &ingest.S3Reader{
				API: clients.Objects, Bucket: bucket, Prefix: prefix,
				AccountID: cfg.AccountID, Region: cfg.Region, Format: format,
			}
		} else if cfg.FlowLogs != "" {
			source = &ingest.CloudWatchReader{
				API: clients.Logs, Group: cfg.FlowLogs, Format: format,
			}
		}
	}

	report := preflight.RunWith(ctx, pf, source, namer, regions)
	fmt.Fprint(stdout, report.String())
	if !report.Ready() {
		return errors.New("preflight checks did not pass")
	}
	return nil
}

const explanation = `custos-collector

WHAT IT READS
  VPC Flow Logs        network metadata: addresses, ports, byte counts, timings
  CloudTrail           which principal is attached to which network interface
  IAM (read-only)      role tags, IAM paths, and attached policy actions
  EC2 interfaces       the name of an address a workload talked to, so the
                       register can say "billing-api" instead of "10.0.4.21".
                       Only addresses already seen in your flow logs, and only
                       names from an ENI's Name tag or AWS's own description —
                       a description AWS did not write is not sent anywhere.
  ALB access logs      optional: when a request arrived and how large it was

RUN --check BEFORE THE FIRST SCAN
  Among other things it names internal addresses that send far more than they
  receive. That is the shape of model traffic, and if one of them is a
  self-hosted model gateway then every agent behind it is invisible to us
  until you say so. Declaring it is one command and it is the single most
  likely reason a scan comes back emptier than you expected.

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
  CUSTOS_FLOW_LOG_FORMAT
                       the format your flow log is written in, if it is not
                       the one our terraform module configures. Only needed
                       for CloudWatch: objects delivered to S3 name their own
                       fields and we read that. Six fields are required —
                       interface-id, srcaddr, dstaddr, bytes, start, end —
                       and --check says what any others cost you.
  CUSTOS_ACCESS_LOGS   s3://bucket/prefix for ALB logs  (optional, lifts recall
                       from 60% to 100% on our test corpus)
  CUSTOS_ACCOUNT_ID    account being scanned
  CUSTOS_WINDOW        collection window, default 1h
  CUSTOS_ROLE_ARN      cross-account role to assume     (optional)
  CUSTOS_EXTERNAL_ID   required whenever a role is assumed
  CUSTOS_DAEMON=1      collect on a schedule instead of once
  CUSTOS_STATE_PATH    where the collection cursor lives across restarts
  CUSTOS_DRY_RUN=1     read and print, never send

RUN THIS FIRST
  ./custos-collector --check

  Every onboarding failure produces the same symptom: a report with no
  findings. --check names which one it is, before you spend an hour reading an
  empty report.

With no endpoint or token configured this binary does nothing at all.
`
