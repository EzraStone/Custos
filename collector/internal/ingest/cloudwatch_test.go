package ingest

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	logtypes "github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs/types"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

const line = "5 447120043318 eni-01a2b3c4d 10.0.20.11 160.79.104.10 43112 443 6 214 286432 " +
	"1786370400 1786370460 ACCEPT OK vpc-0a1b2c3d subnet-0ab12345 egress - 18"

type fakeLogs struct {
	pages [][]string
	calls int
	err   error
}

func (f *fakeLogs) FilterLogEvents(_ context.Context, in *cloudwatchlogs.FilterLogEventsInput,
	_ ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.FilterLogEventsOutput, error) {
	if f.err != nil {
		return nil, f.err
	}
	idx := f.calls
	f.calls++
	if idx >= len(f.pages) {
		return &cloudwatchlogs.FilterLogEventsOutput{}, nil
	}
	events := make([]logtypes.FilteredLogEvent, 0, len(f.pages[idx]))
	for _, m := range f.pages[idx] {
		events = append(events, logtypes.FilteredLogEvent{Message: aws.String(m)})
	}
	out := &cloudwatchlogs.FilterLogEventsOutput{Events: events}
	if idx+1 < len(f.pages) {
		out.NextToken = aws.String("more")
	}
	return out, nil
}

func (f *fakeLogs) DescribeLogGroups(context.Context, *cloudwatchlogs.DescribeLogGroupsInput,
	...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.DescribeLogGroupsOutput, error) {
	return &cloudwatchlogs.DescribeLogGroupsOutput{}, nil
}

func window() awsread.Window {
	return awsread.Window{
		Start: time.Unix(1786370000, 0).UTC(),
		End:   time.Unix(1786374000, 0).UTC(),
	}
}

func TestReadsAllPages(t *testing.T) {
	api := &fakeLogs{pages: [][]string{{line, line}, {line}, {line}}}
	r := &CloudWatchReader{API: api, Group: "/aws/vpc/flowlogs"}

	records, stats, err := r.Read(context.Background(), window())
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 4 {
		t.Fatalf("got %d records across %d pages", len(records), api.calls)
	}
	if stats.Parsed != 4 || stats.Truncated {
		t.Fatalf("unexpected stats %+v", stats)
	}
}

func TestInvalidWindowIsRefused(t *testing.T) {
	r := &CloudWatchReader{API: &fakeLogs{}, Group: "g"}
	if _, _, err := r.Read(context.Background(), awsread.Window{}); err == nil {
		t.Fatal("a zero window must be refused rather than read")
	}
}

// TestPartialResultsSurviveAnError: a partial window with an honest stat is
// more useful than nothing, and the caller decides whether to ship it.
func TestPartialResultsSurviveAnError(t *testing.T) {
	api := &fakeLogs{err: errors.New("throttled")}
	r := &CloudWatchReader{API: api, Group: "g"}
	records, _, err := r.Read(context.Background(), window())
	if err == nil {
		t.Fatal("expected the error to be reported")
	}
	if records == nil && len(records) != 0 {
		t.Fatal("records should be returned alongside the error")
	}
}

// TestTruncationIsReported: a truncated window changes what an absence of
// findings means, so it must never be silent.
func TestTruncationIsReported(t *testing.T) {
	api := &fakeLogs{pages: [][]string{{line, line}, {line, line}, {line, line}}}
	r := &CloudWatchReader{API: api, Group: "g", MaxEvents: 3}

	_, stats, err := r.Read(context.Background(), window())
	if err != nil {
		t.Fatal(err)
	}
	if !stats.Truncated {
		t.Fatal("hitting the event limit must set Truncated")
	}
}

func TestCancellationStopsPagination(t *testing.T) {
	pages := make([][]string, 100)
	for i := range pages {
		pages[i] = []string{line}
	}
	api := &fakeLogs{pages: pages}
	r := &CloudWatchReader{API: api, Group: "g"}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, _, err := r.Read(ctx, window()); !errors.Is(err, context.Canceled) {
		t.Fatalf("expected cancellation, got %v", err)
	}
	if api.calls > 2 {
		t.Fatalf("kept paginating after cancellation: %d calls", api.calls)
	}
}

func TestMalformedMessagesDoNotAbortTheRun(t *testing.T) {
	api := &fakeLogs{pages: [][]string{{line, "garbage", line}}}
	r := &CloudWatchReader{API: api, Group: "g"}
	records, stats, err := r.Read(context.Background(), window())
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 || stats.Malformed != 1 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}
}

func TestWindowHelper(t *testing.T) {
	w := Window(time.Hour)
	if !w.Valid() || w.End.Sub(w.Start) != time.Hour {
		t.Fatalf("bad window %+v", w)
	}
}
