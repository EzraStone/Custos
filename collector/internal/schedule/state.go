// Package schedule turns the collector from a command into a service.
//
// The whole difficulty is one thing: knowing which window to collect next.
//
// Naive scheduling — "every hour, collect the last hour" — loses data on every
// restart, every deploy, and every run that takes longer than its interval. The
// gaps are invisible: the scan that follows simply reports fewer agents, and
// nobody can tell that from an account that has fewer agents.
//
// So the collector records the end of the last window it successfully shipped,
// and the next window starts exactly there. A restart after four hours down
// catches up in four windows rather than pretending the gap did not happen.
package schedule

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

// MaxCatchUp bounds how far back a restart will reach.
//
// A collector that was down for a month should not replay a month of flow logs
// on startup: the logs have usually aged out, the API calls cost the customer
// money, and the resulting scan describes an account that no longer exists.
// Beyond this it starts fresh and says so.
const MaxCatchUp = 24 * time.Hour

// State is what survives a restart. Deliberately one field — anything else here
// is state we would have to keep correct across versions.
type State struct {
	// LastWindowEnd is the end of the most recent window shipped successfully.
	// Never advanced on a failed send: a window that did not arrive has not
	// been collected.
	LastWindowEnd time.Time `json:"last_window_end"`
}

// Store persists State to a file.
type Store struct {
	Path string
}

func (s Store) Load() (State, error) {
	raw, err := os.ReadFile(s.Path)
	if os.IsNotExist(err) {
		// A first run is not an error. It starts one interval back.
		return State{}, nil
	}
	if err != nil {
		return State{}, fmt.Errorf("reading %s: %w", s.Path, err)
	}

	var state State
	if err := json.Unmarshal(raw, &state); err != nil {
		// A corrupt state file must not stop collection. Losing the cursor
		// costs one window of overlap; refusing to run costs all of them.
		return State{}, nil
	}
	return state, nil
}

// Save writes state atomically.
//
// Write-then-rename, because a torn state file is worse than none: it either
// fails to parse, which we recover from, or it parses to a timestamp that was
// never true, which silently skips a window.
func (s Store) Save(state State) error {
	if dir := filepath.Dir(s.Path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			return err
		}
	}

	raw, err := json.Marshal(state)
	if err != nil {
		return err
	}

	temp := s.Path + ".tmp"
	if err := os.WriteFile(temp, raw, 0o640); err != nil {
		return err
	}
	return os.Rename(temp, s.Path)
}

// Next returns the window to collect now, given the last one shipped.
//
// Three cases, each a data-loss bug if handled wrongly:
//
//	no prior state     one interval back from now — a first run has no gap
//	a recent cursor    from the cursor to now, so nothing between is missed
//	an ancient cursor  MaxCatchUp back, because replaying a month is worse
//	                   than admitting the gap
func Next(state State, interval time.Duration, now time.Time) (awsread.Window, bool) {
	now = now.UTC()

	if state.LastWindowEnd.IsZero() {
		return awsread.Window{Start: now.Add(-interval), End: now}, true
	}

	start := state.LastWindowEnd.UTC()
	if oldest := now.Add(-MaxCatchUp); start.Before(oldest) {
		start = oldest
	}

	if !start.Before(now) {
		// The cursor is at or ahead of now, which happens when a run finishes
		// faster than its interval. Collecting a zero-width window would ship
		// an empty batch and advance the cursor over data that had not
		// arrived yet.
		return awsread.Window{}, false
	}

	return awsread.Window{Start: start, End: now}, true
}

// Behind reports how far the cursor lags, for logging.
//
// A number that keeps climbing means collection is slower than its interval,
// which is a capacity problem that otherwise surfaces as unexplained gaps.
func Behind(state State, now time.Time) time.Duration {
	if state.LastWindowEnd.IsZero() {
		return 0
	}
	return now.UTC().Sub(state.LastWindowEnd.UTC())
}
