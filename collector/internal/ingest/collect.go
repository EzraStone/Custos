package ingest

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// FlowSource reads flow records for a window. Satisfied by CloudWatchReader and
// S3Reader, so the orchestrator does not care which destination the customer
// configured.
type FlowSource interface {
	Read(context.Context, awsread.Window) ([]wire.FlowRecord, flowlogs.Stats, error)
}

// Collector assembles one batch from a customer account.
//
// The order matters and is not arbitrary: flow records name interfaces,
// interfaces name principals, and principals have policies. Each step narrows
// what the next one has to look up, which is what keeps the number of billable
// API calls proportional to the number of workloads rather than to the size of
// the account.
type Collector struct {
	Flows    FlowSource
	Network  awsread.NetworkAPI
	Identity awsread.IdentityAPI

	AccountID string
	Region    string
}

// Report is what a human needs to judge whether a scan is trustworthy.
//
// It is separate from the batch because none of it is telemetry — it describes
// the collection, not the account, and it is printed locally rather than being
// the basis of a finding.
type Report struct {
	Stats      flowlogs.Stats
	Interfaces int
	Principals int
	Degraded   []Attribution
	Errors     []string
}

// Trustworthy reports whether the scan covered enough to make an absence of
// findings meaningful.
func (r Report) Trustworthy() bool {
	return !r.Stats.Truncated && r.Stats.Coverage() > 0.95 && r.Stats.SkipData == 0
}

// Summary renders the report for a terminal.
func (r Report) Summary() string {
	var b strings.Builder
	fmt.Fprintf(&b, "parsed %d of %d flow log lines (%.1f%% coverage)\n",
		r.Stats.Parsed, r.Stats.Lines, r.Stats.Coverage()*100)
	fmt.Fprintf(&b, "resolved %d principals across %d interfaces\n",
		r.Principals, r.Interfaces)

	if r.Stats.SkipData > 0 {
		fmt.Fprintf(&b, "WARNING: %d SKIPDATA lines — AWS dropped records it could not "+
			"capture, so this account's traffic is under-represented\n", r.Stats.SkipData)
	}
	if r.Stats.Truncated {
		fmt.Fprintf(&b, "WARNING: hit the event limit — this window is partial and an "+
			"absence of findings means less than it would otherwise\n")
	}
	if n := len(r.Degraded); n > 0 {
		fmt.Fprintf(&b, "%d interfaces could not be attributed to a principal:\n", n)
		for i, d := range r.Degraded {
			if i == 5 {
				fmt.Fprintf(&b, "  ... and %d more\n", n-i)
				break
			}
			fmt.Fprintf(&b, "  %s (%s): %s\n", d.InterfaceID, d.Compute, d.Degraded)
		}
	}
	for _, e := range r.Errors {
		fmt.Fprintf(&b, "error: %s\n", e)
	}
	return b.String()
}

// Collect reads one window and assembles a batch.
//
// Failures in attribution and identity degrade the batch rather than aborting
// it. Flow records with unresolved principals are still useful — the control
// plane reports them as unattributed findings (SEC-20) — and a scan that
// returns nothing because one IAM call failed is a scan the customer will not
// run twice.
func (c *Collector) Collect(ctx context.Context, w awsread.Window) (wire.Batch, Report, error) {
	batch := wire.Batch{
		AccountID:   c.AccountID,
		Region:      c.Region,
		WindowStart: w.Start,
		WindowEnd:   w.End,
	}
	var report Report

	if c.Flows == nil {
		return batch, report, fmt.Errorf("no flow log source configured")
	}

	records, stats, err := c.Flows.Read(ctx, w)
	report.Stats = stats
	if err != nil {
		// Keep what was read. The caller decides whether a partial window is
		// worth shipping.
		report.Errors = append(report.Errors, err.Error())
	}
	batch.Flows = records

	interfaceIDs := distinctInterfaces(records)
	report.Interfaces = len(interfaceIDs)
	if len(interfaceIDs) == 0 || c.Network == nil {
		return batch, report, nil
	}

	resolver := &Resolver{API: c.Network, AccountID: c.AccountID}
	attributions, err := resolver.Resolve(ctx, interfaceIDs)
	if err != nil {
		report.Errors = append(report.Errors, err.Error())
	}

	resolved, degraded := Attachments(attributions)
	batch.Attachments = resolved
	report.Degraded = degraded

	if c.Identity == nil {
		return batch, report, nil
	}

	reader := &IdentityReader{API: c.Identity}
	for _, principal := range distinctPrincipals(resolved) {
		facts, err := reader.Read(ctx, principal)
		if err != nil {
			// A role we cannot read still produces a finding, without the
			// blast radius. Better than dropping the agent entirely.
			report.Errors = append(report.Errors, err.Error())
		}
		facts.Compute = computeFor(resolved, principal)
		batch.Principals = append(batch.Principals, facts)
	}
	report.Principals = len(batch.Principals)

	return batch, report, nil
}

// DistinctInterfaces returns the unique interface IDs in a set of records.
// Exported so a file-based run can report the same interface count as an AWS
// run, rather than reporting zero because it never looked.
func DistinctInterfaces(records []wire.FlowRecord) []string {
	return distinctInterfaces(records)
}

func distinctInterfaces(records []wire.FlowRecord) []string {
	seen := map[string]bool{}
	var out []string
	for _, r := range records {
		if r.InterfaceID != "" && !seen[r.InterfaceID] {
			seen[r.InterfaceID] = true
			out = append(out, r.InterfaceID)
		}
	}
	sort.Strings(out)
	return out
}

func distinctPrincipals(attachments []wire.Attachment) []string {
	seen := map[string]bool{}
	var out []string
	for _, a := range attachments {
		if a.Principal != "" && !seen[a.Principal] {
			seen[a.Principal] = true
			out = append(out, a.Principal)
		}
	}
	sort.Strings(out)
	return out
}

func computeFor(attachments []wire.Attachment, principal string) string {
	for _, a := range attachments {
		if a.Principal == principal && a.Compute != "" {
			return a.Compute
		}
	}
	return ""
}

// DefaultWindow returns the collection window ending now.
func DefaultWindow(d time.Duration) awsread.Window { return Window(d) }
