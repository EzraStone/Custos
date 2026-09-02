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
	Flows      FlowSource
	Requests   RequestSource
	Network    awsread.NetworkAPI
	Identity   awsread.IdentityAPI
	Serverless awsread.ServerlessAPI
	Trail      awsread.TrailAPI

	AccountID string
	Region    string
}

// RequestSource reads inbound requests for a window. Optional: many customers
// will not hand over load balancer logs, and the classifier degrades honestly
// without them rather than guessing.
type RequestSource interface {
	Read(context.Context, awsread.Window) ([]wire.InboundRequest, error)
}

// Report is what a human needs to judge whether a scan is trustworthy.
//
// It is separate from the batch because none of it is telemetry — it describes
// the collection, not the account, and it is printed locally rather than being
// the basis of a finding.
type Report struct {
	Stats       flowlogs.Stats
	Interfaces  int
	Principals  int
	Requests    int
	HaveALBLogs bool

	// TrailResolved counts interfaces attributed only because CloudTrail saw
	// the address. Reported because it is the weakest attribution path, and a
	// scan leaning on it heavily is a scan whose owners are less certain than
	// the rest.
	TrailResolved int

	// Shortened records that the window was cut back to what was actually
	// read, so the cursor resumes from there. Reported because it means
	// collection is running behind and the interval should probably be shorter.
	Shortened bool

	// Destinations counts the addresses that could be given a name, out of
	// PeerAddresses that were asked about. The rest appear in the register as
	// addresses, so the ratio is the difference between a scope an operator
	// can read and one they cannot — printed every run rather than kept for a
	// warning, because it is a number the customer can act on.
	Destinations  int
	PeerAddresses int

	Degraded []Attribution
	Errors   []string
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
	if r.PeerAddresses > 0 {
		fmt.Fprintf(&b, "named %d of %d internal destinations\n",
			r.Destinations, r.PeerAddresses)
	}

	if r.HaveALBLogs {
		fmt.Fprintf(&b, "correlated against %d inbound requests\n", r.Requests)
	} else {
		// Worth saying every run. This is the difference between finding a
		// customer's low-volume agents and surfacing them as maybes.
		fmt.Fprintf(&b, "NOTE: no load balancer access logs configured — the strongest "+
			"classifier signal is unavailable, so low-volume agents will surface for "+
			"review rather than as findings\n")
	}

	if r.Stats.SkipData > 0 {
		fmt.Fprintf(&b, "WARNING: %d SKIPDATA lines — AWS dropped records it could not "+
			"capture, so this account's traffic is under-represented\n", r.Stats.SkipData)
	}
	if r.Stats.Truncated && r.Shortened {
		fmt.Fprintf(&b, "hit the record limit; window shortened to what was read — "+
			"no data lost, but consider a shorter collection interval\n")
	} else if r.Stats.Truncated {
		fmt.Fprintf(&b, "WARNING: hit the record limit and the window could not be "+
			"shortened — this window is partial and an absence of findings means "+
			"less than it would otherwise\n")
	}
	if r.TrailResolved > 0 {
		fmt.Fprintf(&b, "%d interfaces attributed via CloudTrail rather than their "+
			"instance profile; those owners are less certain\n", r.TrailResolved)
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

	// A window that hit the record limit is shortened to what was actually
	// read, rather than shipped as if it covered the whole span.
	//
	// This is the difference between a busy hour costing extra windows and a
	// busy hour losing data. Shipping a truncated window as if it were
	// complete advances the collection cursor past records that were never
	// read, and they are gone — the next scan reports fewer agents and nothing
	// distinguishes that from an account with fewer agents.
	if stats.Truncated {
		if end, ok := ShortenTo(records); ok {
			w.End = end
			report.Shortened = true
		}
	}
	batch.WindowEnd = w.End
	batch.Collection = wire.Collection{
		LinesRead:      int64(stats.Lines),
		LinesParsed:    int64(stats.Parsed),
		LinesMalformed: int64(stats.Malformed),
		RecordsSkipped: int64(stats.SkipData + stats.NoData),
		Truncated:      stats.Truncated,
	}
	if err != nil {
		// Keep what was read. The caller decides whether a partial window is
		// worth shipping.
		report.Errors = append(report.Errors, err.Error())
	}
	batch.Flows = records

	if c.Requests != nil {
		requests, err := c.Requests.Read(ctx, w)
		if err != nil {
			report.Errors = append(report.Errors, err.Error())
		}
		batch.Requests = requests
		report.Requests = len(requests)
		report.HaveALBLogs = true
		batch.Collection.HaveAccessLogs = true
	}

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

	// Lambda and ECS interfaces need extra calls that EC2 does not. Doing it
	// here rather than inside Resolve keeps the EC2 path free of the API
	// surface it does not use.
	if c.Serverless != nil {
		attributions = NewServerless(c.Serverless).Enrich(ctx, attributions)
	}

	// CloudTrail is the last resort and the only one that does not depend on
	// resolving an interface. Applied after the others so it fills gaps rather
	// than overriding attribution we are more confident in — an ENI attached
	// to an instance profile is a stronger claim than an address seen making a
	// Bedrock call, because addresses are recycled.
	if c.Trail != nil {
		attributions = c.correlateFromTrail(ctx, w, attributions, &report)
	}

	resolved, degraded := Attachments(attributions)
	batch.Attachments = resolved
	report.Degraded = degraded

	// What the workloads reached, named. This is the half of the register an
	// operator is asked to approve, and without it the scope is a list of
	// addresses. A failure here costs the names and not the scan: the control
	// plane falls back to showing the address, which is what it did before
	// this existed.
	destinations := &DestinationResolver{API: c.Network}
	peers := internalOnly(peerAddresses(records))
	named, err := destinations.Resolve(ctx, peers)
	if err != nil {
		report.Errors = append(report.Errors, err.Error())
	}
	batch.Destinations = named
	report.Destinations = len(named)
	report.PeerAddresses = len(peers)

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

// ShortenTo returns the end of the last record read, so a window that hit its
// limit can be cut back to cover exactly what it collected.
//
// Uses the maximum record end rather than the last record in the slice: flow
// log pages are not strictly ordered, and taking the last one would cut the
// window short of records already in hand — which would then be re-collected
// next run, forever, because the cursor never reaches them.
func ShortenTo(records []wire.FlowRecord) (time.Time, bool) {
	var latest time.Time
	for _, r := range records {
		if r.End.After(latest) {
			latest = r.End
		}
	}
	return latest, !latest.IsZero()
}

// correlateFromTrail fills in principals for interfaces nothing else resolved.
func (c *Collector) correlateFromTrail(
	ctx context.Context, w awsread.Window, attributions []Attribution, report *Report,
) []Attribution {
	unresolved := 0
	for _, a := range attributions {
		if a.Principal == "" {
			unresolved++
		}
	}
	if unresolved == 0 {
		// Nothing to fill in. Skipping the lookup entirely is the difference
		// between CloudTrail costing nothing on a well-tagged account and
		// costing a dozen API calls every window forever.
		return attributions
	}

	byAddress, err := (&TrailCorrelator{API: c.Trail}).Correlate(ctx, w)
	if err != nil {
		report.Errors = append(report.Errors, "cloudtrail correlation: "+err.Error())
	}
	if len(byAddress) == 0 {
		return attributions
	}

	filled := 0
	for i := range attributions {
		if attributions[i].Principal != "" {
			continue
		}
		if principal, ok := byAddress[attributions[i].Address]; ok {
			attributions[i].Principal = principal
			attributions[i].Degraded = ""
			filled++
		}
	}
	report.TrailResolved = filled
	return attributions
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

// peerAddresses is every address the account's workloads talked to.
//
// The peer is the destination on the way out and the source on the way back,
// so both ends are collected: a workload that only ever received from an
// address still reached it.
func peerAddresses(records []wire.FlowRecord) []string {
	seen := map[string]bool{}
	var out []string
	for _, r := range records {
		peer := r.DstAddr
		if r.Direction == wire.Ingress {
			peer = r.SrcAddr
		}
		if peer != "" && !seen[peer] {
			seen[peer] = true
			out = append(out, peer)
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
