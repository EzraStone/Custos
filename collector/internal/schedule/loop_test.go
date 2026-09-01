package schedule

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

type clock struct {
	mu sync.Mutex
	at time.Time
}

func (c *clock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.at
}

func (c *clock) advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.at = c.at.Add(d)
}

func newLoop(t *testing.T, collect Collect, fast bool) (Options, *clock) {
	t.Helper()
	c := &clock{at: now}
	opts := Options{
		// A millisecond interval keeps the test fast. Window arithmetic is
		// driven by the injected clock, not by wall time, so the two are
		// independent.
		Interval:   time.Millisecond,
		State:      Store{Path: filepath.Join(t.TempDir(), "state.json")},
		Now:        c.now,
		Jitter:     -1,
		MaxBackoff: time.Millisecond,
		Log:        io.Discard,
	}
	if !fast {
		opts.Interval = time.Hour
	}
	return opts, c
}

func runFor(t *testing.T, opts Options, collect Collect, d time.Duration) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), d)
	defer cancel()
	if err := Run(ctx, opts, collect); !errors.Is(err, context.DeadlineExceeded) &&
		!errors.Is(err, context.Canceled) {
		t.Fatalf("unexpected exit: %v", err)
	}
}

func TestTheCursorAdvancesAfterASuccessfulCollection(t *testing.T) {
	var mu sync.Mutex
	var windows []awsread.Window

	opts, c := newLoop(t, nil, true)
	collect := func(_ context.Context, w awsread.Window) error {
		mu.Lock()
		windows = append(windows, w)
		mu.Unlock()
		c.advance(time.Hour)
		return nil
	}

	runFor(t, opts, collect, 60*time.Millisecond)

	mu.Lock()
	defer mu.Unlock()
	if len(windows) < 2 {
		t.Fatalf("expected several windows, got %d", len(windows))
	}
	for i := 1; i < len(windows); i++ {
		if !windows[i].Start.Equal(windows[i-1].End) {
			t.Fatalf("gap between window %d and %d", i-1, i)
		}
	}

	state, err := opts.State.Load()
	if err != nil {
		t.Fatal(err)
	}
	if !state.LastWindowEnd.Equal(windows[len(windows)-1].End) {
		t.Fatal("cursor does not match the last collected window")
	}
}

// A window that did not ship has not been collected. Advancing over it loses
// the data silently.
func TestTheCursorDoesNotAdvanceOnFailure(t *testing.T) {
	opts, _ := newLoop(t, nil, true)
	collect := func(context.Context, awsread.Window) error {
		return errors.New("throttled")
	}

	runFor(t, opts, collect, 30*time.Millisecond)

	state, err := opts.State.Load()
	if err != nil {
		t.Fatal(err)
	}
	if !state.LastWindowEnd.IsZero() {
		t.Fatalf("cursor advanced past a failed window: %v", state.LastWindowEnd)
	}
}

func TestAFailedWindowIsRetried(t *testing.T) {
	var mu sync.Mutex
	var attempts []awsread.Window

	opts, _ := newLoop(t, nil, true)
	collect := func(_ context.Context, w awsread.Window) error {
		mu.Lock()
		attempts = append(attempts, w)
		mu.Unlock()
		return errors.New("still down")
	}

	runFor(t, opts, collect, 40*time.Millisecond)

	mu.Lock()
	defer mu.Unlock()
	if len(attempts) < 2 {
		t.Fatalf("expected retries, got %d attempts", len(attempts))
	}
	// Every retry covers the same start, because the cursor never moved.
	for i := 1; i < len(attempts); i++ {
		if !attempts[i].Start.Equal(attempts[0].Start) {
			t.Fatal("a retry skipped the failed window")
		}
	}
}

func TestCancellationStopsTheLoop(t *testing.T) {
	opts, _ := newLoop(t, nil, false)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if err := Run(ctx, opts, func(context.Context, awsread.Window) error {
		t.Fatal("must not collect after cancellation")
		return nil
	}); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}

// Backoff exists so that failing fast against a throttled API does not make
// the throttling worse.
func TestBackoffGrowsWithConsecutiveFailuresAndIsBounded(t *testing.T) {
	opts := Options{Interval: time.Minute, MaxBackoff: 10 * time.Minute, Jitter: -1}
	opts.withDefaults()

	first := delay(opts, 1)
	second := delay(opts, 2)
	far := delay(opts, 50)

	if second <= first {
		t.Fatalf("backoff did not grow: %v then %v", first, second)
	}
	if far > opts.MaxBackoff {
		t.Fatalf("backoff exceeded its bound: %v", far)
	}
}

// A fleet of collectors that synchronise on the minute they were deployed
// produces a thundering herd against each customer's rate limits.
func TestJitterIsPositiveAndBounded(t *testing.T) {
	span := time.Minute
	for i := 0; i < 200; i++ {
		got := jitter(span)
		if got < 0 || got >= span {
			t.Fatalf("jitter %v outside [0, %v)", got, span)
		}
	}
	if jitter(0) != 0 {
		t.Fatal("zero span must produce no jitter")
	}
}

func TestDefaultsAreApplied(t *testing.T) {
	opts := Options{}
	opts.withDefaults()
	if opts.Interval != DefaultInterval || opts.Jitter != DefaultJitter ||
		opts.MaxBackoff != DefaultMaxBackoff || opts.Now == nil || opts.Log == nil {
		t.Fatalf("defaults not applied: %+v", opts)
	}
}
