package schedule

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

var now = time.Date(2026, 8, 10, 12, 0, 0, 0, time.UTC)

func TestAFirstRunStartsOneIntervalBack(t *testing.T) {
	window, ok := Next(State{}, time.Hour, now)
	if !ok {
		t.Fatal("a first run must collect something")
	}
	if !window.End.Equal(now) || !window.Start.Equal(now.Add(-time.Hour)) {
		t.Fatalf("unexpected window %v..%v", window.Start, window.End)
	}
}

// The gap is invisible otherwise: the next scan simply reports fewer agents,
// which is indistinguishable from an account that has fewer agents.
func TestARestartResumesFromTheCursorRatherThanLosingTheGap(t *testing.T) {
	state := State{LastWindowEnd: now.Add(-4 * time.Hour)}

	window, ok := Next(state, time.Hour, now)
	if !ok {
		t.Fatal("expected a catch-up window")
	}
	if !window.Start.Equal(now.Add(-4 * time.Hour)) {
		t.Fatalf("resumed from %v, losing the gap", window.Start)
	}
	if !window.End.Equal(now) {
		t.Fatalf("window should extend to now, got %v", window.End)
	}
}

// Replaying a month costs the customer API calls, reads logs that have aged
// out, and describes an account that no longer exists.
func TestAnAncientCursorIsClampedRatherThanReplayed(t *testing.T) {
	state := State{LastWindowEnd: now.AddDate(0, -1, 0)}

	window, ok := Next(state, time.Hour, now)
	if !ok {
		t.Fatal("expected a window")
	}
	if window.End.Sub(window.Start) > MaxCatchUp {
		t.Fatalf("window spans %v, beyond the %v bound", window.End.Sub(window.Start), MaxCatchUp)
	}
}

// Collecting a zero-width window ships an empty batch and advances the cursor
// over data that has not arrived yet.
func TestNothingToCollectYetIsNotAWindow(t *testing.T) {
	if _, ok := Next(State{LastWindowEnd: now}, time.Hour, now); ok {
		t.Fatal("a cursor at now must produce no window")
	}
	if _, ok := Next(State{LastWindowEnd: now.Add(time.Hour)}, time.Hour, now); ok {
		t.Fatal("a cursor ahead of now must produce no window")
	}
}

func TestStateRoundTripsThroughAFile(t *testing.T) {
	store := Store{Path: filepath.Join(t.TempDir(), "nested", "state.json")}
	want := State{LastWindowEnd: now}

	if err := store.Save(want); err != nil {
		t.Fatal(err)
	}
	got, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if !got.LastWindowEnd.Equal(want.LastWindowEnd) {
		t.Fatalf("got %v want %v", got.LastWindowEnd, want.LastWindowEnd)
	}
}

func TestAMissingStateFileIsNotAnError(t *testing.T) {
	store := Store{Path: filepath.Join(t.TempDir(), "absent.json")}
	state, err := store.Load()
	if err != nil {
		t.Fatalf("a first run must not error: %v", err)
	}
	if !state.LastWindowEnd.IsZero() {
		t.Fatal("expected a zero cursor")
	}
}

// Losing the cursor costs one window of overlap. Refusing to run costs all of
// them.
func TestACorruptStateFileDoesNotStopCollection(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	if err := os.WriteFile(path, []byte("{ not json"), 0o600); err != nil {
		t.Fatal(err)
	}

	state, err := Store{Path: path}.Load()
	if err != nil {
		t.Fatalf("a corrupt cursor must not be fatal: %v", err)
	}
	if !state.LastWindowEnd.IsZero() {
		t.Fatal("expected to fall back to a first run")
	}
}

// A torn file either fails to parse, which we recover from, or parses to a
// timestamp that was never true, which silently skips a window.
func TestSaveLeavesNoTemporaryFileBehind(t *testing.T) {
	dir := t.TempDir()
	store := Store{Path: filepath.Join(dir, "state.json")}
	if err := store.Save(State{LastWindowEnd: now}); err != nil {
		t.Fatal(err)
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if filepath.Ext(entry.Name()) == ".tmp" {
			t.Fatalf("temporary file left behind: %s", entry.Name())
		}
	}
}

func TestConsecutiveWindowsDoNotOverlapOrGap(t *testing.T) {
	state := State{}
	at := now
	var windows []struct{ start, end time.Time }

	for i := 0; i < 5; i++ {
		window, ok := Next(state, time.Hour, at)
		if !ok {
			t.Fatalf("iteration %d produced no window", i)
		}
		windows = append(windows, struct{ start, end time.Time }{window.Start, window.End})
		state.LastWindowEnd = window.End
		at = at.Add(time.Hour)
	}

	for i := 1; i < len(windows); i++ {
		if !windows[i].start.Equal(windows[i-1].end) {
			t.Fatalf("window %d starts at %v but %d ended at %v",
				i, windows[i].start, i-1, windows[i-1].end)
		}
	}
}

func TestBehindReportsCursorLag(t *testing.T) {
	if got := Behind(State{}, now); got != 0 {
		t.Fatalf("a first run is not behind, got %v", got)
	}
	if got := Behind(State{LastWindowEnd: now.Add(-3 * time.Hour)}, now); got != 3*time.Hour {
		t.Fatalf("got %v", got)
	}
}
