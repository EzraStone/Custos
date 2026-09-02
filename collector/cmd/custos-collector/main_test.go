package main

import (
	"os"
	"strings"
	"testing"
)

func capture(t *testing.T, args []string) (string, error) {
	t.Helper()
	out, err := os.CreateTemp(t.TempDir(), "out")
	if err != nil {
		t.Fatal(err)
	}
	errOut, err := os.CreateTemp(t.TempDir(), "err")
	if err != nil {
		t.Fatal(err)
	}
	runErr := run(args, out, errOut)
	body, _ := os.ReadFile(out.Name())
	stderrBody, _ := os.ReadFile(errOut.Name())
	return string(body) + string(stderrBody), runErr
}

// TestRunningByAccidentDoesNothing is SEC-19 at the entry point. A binary that
// ends up on the wrong host must have a boring answer to "what happened".
func TestRunningByAccidentDoesNothing(t *testing.T) {
	for _, key := range []string{"CUSTOS_ENDPOINT", "CUSTOS_TOKEN", "CUSTOS_FLOW_LOGS", "CUSTOS_DRY_RUN"} {
		t.Setenv(key, "")
	}
	out, err := capture(t, nil)
	if err != nil {
		t.Fatalf("running unconfigured must exit cleanly, got %v", err)
	}
	if !strings.Contains(out, "SEC-19") {
		t.Fatalf("expected a SEC-19 explanation, got %q", out)
	}
}

func TestExplainDescribesWhatIsSentAndWhatCannotBe(t *testing.T) {
	out, err := capture(t, []string{"--explain"})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"WHAT IT READS", "WHAT IT SENDS", "WHAT IT CANNOT DO", "CUSTOS_DRY_RUN"} {
		if !strings.Contains(out, want) {
			t.Errorf("--explain output missing %q", want)
		}
	}
}

func TestVersionFlag(t *testing.T) {
	out, err := capture(t, []string{"--version"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(out) != Version {
		t.Fatalf("got %q want %q", strings.TrimSpace(out), Version)
	}
}

func TestDryRunPrintsTheBatchAndSendsNothing(t *testing.T) {
	dir := t.TempDir()
	logPath := dir + "/flow.log"
	line := "5 447120043318 eni-01a2b3c4d 10.0.20.11 160.79.104.10 43112 443 6 214 286432 " +
		"1786370400 1786370460 ACCEPT OK vpc-0a1b2c3d subnet-0ab12345 egress - - 18\n"
	if err := os.WriteFile(logPath, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("CUSTOS_DRY_RUN", "1")
	t.Setenv("CUSTOS_FLOW_LOGS", "/aws/vpc/flowlogs")
	t.Setenv("CUSTOS_ENDPOINT", "")
	t.Setenv("CUSTOS_TOKEN", "")

	out, err := capture(t, []string{"--from-file", logPath})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "286432") {
		t.Fatalf("dry run should print the batch, got %q", out)
	}
	if !strings.Contains(out, "nothing was sent") {
		t.Fatal("dry run must state that nothing was sent")
	}
}
