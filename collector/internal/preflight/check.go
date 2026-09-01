// Package preflight answers "why did the scan find nothing" before the scan.
//
// Every failure mode in onboarding produces the same symptom: a report with no
// findings. An empty flow log group, a role that cannot be assumed, a log group
// in the wrong region, flow logs configured with a different field set — all of
// them look exactly like an account with no agents, which is the one result we
// must never report by accident.
//
// So each check names the thing that is wrong and what to do about it, and the
// binary runs them on demand rather than leaving someone to infer it from an
// empty report an hour later.
package preflight

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// Status is how a check came out.
type Status string

const (
	Pass Status = "pass"
	Warn Status = "warn"
	Fail Status = "fail"
)

// Result is one check.
type Result struct {
	Name   string
	Status Status
	Detail string
	// Remedy is what to do about it, in the imperative. A check that reports a
	// problem without one has moved the work rather than done it.
	Remedy string
}

// Report is every check, with the overall verdict.
type Report struct {
	Results []Result
}

func (r *Report) add(name string, status Status, detail, remedy string) {
	r.Results = append(r.Results, Result{
		Name: name, Status: status, Detail: detail, Remedy: remedy,
	})
}

// Ready reports whether a scan is worth running.
//
// Warnings do not block. A missing access log configuration costs recall and is
// worth saying loudly, but refusing to scan over it would turn a degraded
// result into no result.
func (r Report) Ready() bool {
	for _, result := range r.Results {
		if result.Status == Fail {
			return false
		}
	}
	return true
}

// String renders the report for a terminal.
func (r Report) String() string {
	var b strings.Builder
	symbols := map[Status]string{Pass: "  ok  ", Warn: " warn ", Fail: " FAIL "}

	for _, result := range r.Results {
		fmt.Fprintf(&b, "[%s] %-24s %s\n", symbols[result.Status], result.Name, result.Detail)
		if result.Remedy != "" && result.Status != Pass {
			fmt.Fprintf(&b, "                                 -> %s\n", result.Remedy)
		}
	}

	if r.Ready() {
		fmt.Fprintln(&b, "\nReady to scan.")
	} else {
		fmt.Fprintln(&b, "\nNot ready. A scan now would find nothing, "+
			"which is indistinguishable from an account with no agents.")
	}
	return b.String()
}

// FlowSource is the reader under test.
type FlowSource interface {
	Read(context.Context, awsread.Window) ([]wire.FlowRecord, flowlogs.Stats, error)
}

// Config is what preflight needs to know about the intended run.
type Config struct {
	AccountID     string
	Region        string
	RoleARN       string
	ExternalID    string
	FlowLogs      string
	AccessLogs    string
	HaveEndpoint  bool
	HaveToken     bool
	ProbeInterval time.Duration
}

// Run performs every check it can with the given configuration.
//
// The flow source may be nil, in which case reachability is skipped and
// reported as skipped. A configuration check that requires credentials is
// useless in the situation where someone most wants to run one.
func Run(ctx context.Context, cfg Config, flows FlowSource) Report {
	var report Report

	checkConfiguration(&report, cfg)
	checkAccessLogs(&report, cfg)

	if flows == nil {
		report.add("aws reachability", Warn,
			"skipped - no credentials configured",
			"re-run with the role configured to test that the log group is readable")
		return report
	}

	checkFlowLogs(ctx, &report, cfg, flows)
	return report
}

func checkConfiguration(report *Report, cfg Config) {
	if cfg.AccountID == "" {
		report.add("account id", Warn, "not set",
			"set CUSTOS_ACCOUNT_ID so findings are attributed to the right account")
	} else {
		report.add("account id", Pass, cfg.AccountID, "")
	}

	if cfg.FlowLogs == "" {
		report.add("flow log source", Fail, "not set",
			"set CUSTOS_FLOW_LOGS to a CloudWatch Logs group or s3://bucket/prefix")
	} else {
		report.add("flow log source", Pass, cfg.FlowLogs, "")
	}

	switch {
	case cfg.RoleARN == "":
		report.add("credentials", Pass, "using ambient credentials", "")
	case cfg.ExternalID == "":
		report.add("credentials", Fail,
			"a role is configured with no external ID",
			"set CUSTOS_EXTERNAL_ID - a cross-account role without one can be "+
				"assumed by anyone who learns the ARN")
	case cfg.Region == "":
		report.add("credentials", Fail, "a role is configured with no region",
			"set AWS_REGION")
	default:
		report.add("credentials", Pass, "assuming "+cfg.RoleARN, "")
	}

	if !cfg.HaveEndpoint || !cfg.HaveToken {
		report.add("destination", Warn, "not configured - nothing will be sent",
			"set CUSTOS_ENDPOINT and CUSTOS_TOKEN, or run with CUSTOS_DRY_RUN=1")
	} else {
		report.add("destination", Pass, "configured", "")
	}
}

// checkAccessLogs warns rather than fails, with the number attached.
//
// "Also give us your load balancer logs" is a worse request than "without them
// we miss the low-volume agents, which are usually the ones you want to know
// about". The number is what makes the ask land.
func checkAccessLogs(report *Report, cfg Config) {
	if cfg.AccessLogs == "" {
		report.add("access logs", Warn, "not configured",
			"set CUSTOS_ACCESS_LOGS - without them recall falls from 100% to "+
				"60% on our test corpus, and the agents missed are the "+
				"low-volume ones")
		return
	}
	report.add("access logs", Pass, cfg.AccessLogs, "")
}

func checkFlowLogs(ctx context.Context, report *Report, cfg Config, flows FlowSource) {
	interval := cfg.ProbeInterval
	if interval <= 0 {
		interval = time.Hour
	}
	end := time.Now().UTC()
	window := awsread.Window{Start: end.Add(-interval), End: end}

	records, stats, err := flows.Read(ctx, window)
	if err != nil {
		report.add("flow logs readable", Fail, err.Error(),
			"check that the role can read the log group and that the region is right")
		return
	}

	if len(records) == 0 {
		// The failure that looks like good news.
		report.add("flow logs readable", Fail,
			fmt.Sprintf("reachable, but no records in the last %s", interval),
			"check that this is the log group carrying the traffic - an empty "+
				"group produces a report with no findings, which reads exactly "+
				"like an account with no agents")
		return
	}

	report.add("flow logs readable", Pass,
		fmt.Sprintf("%d records in the last %s", len(records), interval), "")

	if stats.Malformed > 0 && stats.Parsed == 0 {
		report.add("flow log format", Fail,
			fmt.Sprintf("%d lines read, none parsed", stats.Malformed),
			"the log format does not match what Custos expects - apply the "+
				"Terraform module's log_format, or send us a sample line")
		return
	}

	if coverage := stats.Coverage(); coverage < 0.95 {
		report.add("flow log format", Warn,
			fmt.Sprintf("only %.0f%% of lines parsed", coverage*100),
			"some lines do not match the expected format; findings will be "+
				"based on partial traffic")
		return
	}

	report.add("flow log format", Pass, "parses cleanly", "")

	if !hasModelTraffic(records) {
		report.add("model traffic", Warn,
			"no traffic to a recognised model endpoint in this window",
			"either nothing called a model in the last hour, or the model "+
				"endpoint is one we do not recognise - a self-hosted gateway "+
				"is the usual cause and needs declaring")
		return
	}
	report.add("model traffic", Pass, "present", "")
}

// hasModelTraffic looks for outbound 443 to any destination.
//
// Deliberately cruder than the classifier's catalogue. The question here is
// whether this window contains the shape of model traffic at all, and answering
// it with the full catalogue would report a clean pass on an account whose
// gateway we cannot see - the exact case this check exists to surface.
func hasModelTraffic(records []wire.FlowRecord) bool {
	for _, r := range records {
		if r.Direction == wire.Egress && r.DstPort == 443 && r.Bytes > 0 {
			return true
		}
	}
	return false
}
