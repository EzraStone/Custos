package schedule

import (
	"context"
	"fmt"
	"io"
	"math/rand/v2"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

// Collect performs one window's work. Returns an error to signal that the
// cursor must not advance.
type Collect func(context.Context, awsread.Window) error

// Options configures the loop.
type Options struct {
	Interval time.Duration
	State    Store
	Now      func() time.Time
	Log      io.Writer

	// Jitter spreads runs so that a fleet of collectors across many customer
	// accounts does not hit AWS on the same second of every hour. Without it,
	// scheduled collectors synchronise on the minute they were deployed and
	// produce a thundering herd against the customer's own rate limits.
	Jitter time.Duration

	// MaxBackoff bounds retry delay after consecutive failures. Failing fast
	// forever against a throttled API makes the throttling worse.
	MaxBackoff time.Duration
}

const (
	DefaultInterval   = time.Hour
	DefaultJitter     = 5 * time.Minute
	DefaultMaxBackoff = 30 * time.Minute
)

func (o *Options) withDefaults() {
	if o.Interval <= 0 {
		o.Interval = DefaultInterval
	}
	if o.Now == nil {
		o.Now = func() time.Time { return time.Now().UTC() }
	}
	if o.Jitter < 0 {
		o.Jitter = 0
	} else if o.Jitter == 0 {
		o.Jitter = DefaultJitter
	}
	if o.MaxBackoff <= 0 {
		o.MaxBackoff = DefaultMaxBackoff
	}
	if o.Log == nil {
		o.Log = io.Discard
	}
}

// Run collects on a schedule until the context is cancelled.
//
// The cursor advances only after a successful collection. That single rule is
// what makes a crash, a deploy, or a throttled hour cost nothing but latency —
// the next run picks up exactly where the last success ended.
func Run(ctx context.Context, opts Options, collect Collect) error {
	opts.withDefaults()

	state, err := opts.State.Load()
	if err != nil {
		return fmt.Errorf("loading collection state: %w", err)
	}

	if behind := Behind(state, opts.Now()); behind > opts.Interval*2 {
		fmt.Fprintf(opts.Log,
			"resuming %s behind; the next window covers the gap\n", behind.Round(time.Minute))
	}

	failures := 0
	for {
		// Checked before collecting, not only after. Otherwise a cancelled
		// collector starts one more window's work on its way out — API calls
		// against a customer's account during a shutdown they asked for.
		if err := ctx.Err(); err != nil {
			return err
		}

		window, ready := Next(state, opts.Interval, opts.Now())

		if ready {
			if err := collect(ctx, window); err != nil {
				failures++
				// Deliberately not advancing the cursor. The window will be
				// collected again, which is why ingestion is idempotent on it.
				fmt.Fprintf(opts.Log, "collection failed (%d consecutive): %v\n", failures, err)
			} else {
				failures = 0
				state.LastWindowEnd = window.End
				if err := opts.State.Save(state); err != nil {
					// The window shipped but the cursor did not persist. The
					// next run re-collects it, which the control plane
					// deduplicates — an overlap, not a loss.
					fmt.Fprintf(opts.Log, "collected but could not save cursor: %v\n", err)
				}
			}
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay(opts, failures)):
		}
	}
}

// delay returns how long to wait before the next attempt.
func delay(opts Options, failures int) time.Duration {
	if failures == 0 {
		return opts.Interval + jitter(opts.Jitter)
	}

	backoff := opts.Interval
	for i := 1; i < failures && backoff < opts.MaxBackoff; i++ {
		backoff *= 2
	}
	if backoff > opts.MaxBackoff {
		backoff = opts.MaxBackoff
	}
	return backoff + jitter(opts.Jitter)
}

// jitter returns a random offset in [0, span).
//
// Positive only. A negative offset could pull a run before its interval, which
// on a throttled API is the opposite of what backoff is for.
func jitter(span time.Duration) time.Duration {
	if span <= 0 {
		return 0
	}
	return time.Duration(rand.Int64N(int64(span)))
}
