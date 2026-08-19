// Package ingest reads telemetry out of a customer's AWS account.
//
// Everything here goes through the read-only interfaces in awsread, so nothing
// in this package can mutate anything. What it can do is read a lot of data
// slowly and expensively, which is its own way of damaging a customer
// relationship — so the readers are bounded, paginated, and report what they
// skipped rather than silently truncating.
package ingest

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// MaxEventsPerRun bounds a single collection.
//
// A busy account produces millions of flow log records an hour. Reading all of
// them would cost the customer money in API calls and cost us nothing useful:
// the classifier works on aggregate byte ratios per principal, which converge
// long before the whole window is read. Hitting this limit is reported, not
// hidden, because a truncated window changes what an absence of findings means.
const MaxEventsPerRun = 2_000_000

// pageSize is the CloudWatch Logs maximum. Fewer, larger pages means fewer
// billable API calls against the customer's account.
const pageSize = 10_000

// CloudWatchReader reads VPC Flow Logs from a CloudWatch Logs group.
type CloudWatchReader struct {
	API       awsread.LogsAPI
	Group     string
	MaxEvents int
}

// Read collects flow records for the window.
func (r *CloudWatchReader) Read(ctx context.Context, w awsread.Window) ([]wire.FlowRecord, flowlogs.Stats, error) {
	if !w.Valid() {
		return nil, flowlogs.Stats{}, fmt.Errorf("invalid window %v..%v", w.Start, w.End)
	}
	limit := r.MaxEvents
	if limit <= 0 {
		limit = MaxEventsPerRun
	}

	var (
		records []wire.FlowRecord
		stats   flowlogs.Stats
		token   *string
	)

	for {
		out, err := r.API.FilterLogEvents(ctx, &cloudwatchlogs.FilterLogEventsInput{
			LogGroupName: aws.String(r.Group),
			StartTime:    aws.Int64(w.Start.UnixMilli()),
			EndTime:      aws.Int64(w.End.UnixMilli()),
			Limit:        aws.Int32(pageSize),
			NextToken:    token,
		})
		if err != nil {
			// Return what was read rather than discarding it. A partial window
			// with an honest stat is more useful than nothing, and the caller
			// decides whether to ship it.
			return records, stats, fmt.Errorf("reading %s: %w", r.Group, err)
		}

		for _, event := range out.Events {
			if event.Message == nil {
				continue
			}
			batch, batchStats, err := flowlogs.Parse(strings.NewReader(*event.Message))
			if err != nil {
				stats.Malformed++
				continue
			}
			records = append(records, batch...)
			stats = mergeStats(stats, batchStats)
		}

		if len(records) >= limit {
			stats.Truncated = true
			break
		}
		if out.NextToken == nil || *out.NextToken == "" {
			break
		}
		token = out.NextToken

		if err := ctx.Err(); err != nil {
			return records, stats, err
		}
	}

	return records, stats, nil
}

func mergeStats(a, b flowlogs.Stats) flowlogs.Stats {
	return flowlogs.Stats{
		Lines:     a.Lines + b.Lines,
		Parsed:    a.Parsed + b.Parsed,
		Skipped:   a.Skipped + b.Skipped,
		NoData:    a.NoData + b.NoData,
		SkipData:  a.SkipData + b.SkipData,
		Malformed: a.Malformed + b.Malformed,
		Truncated: a.Truncated || b.Truncated,
	}
}

// Window returns the collection window ending now.
func Window(d time.Duration) awsread.Window {
	end := time.Now().UTC()
	return awsread.Window{Start: end.Add(-d), End: end}
}
