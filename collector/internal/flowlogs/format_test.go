package flowlogs

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// TestTerraformFormatMatchesParser guards a silent failure mode.
//
// The Terraform module configures the flow log format; this package parses it.
// If they drift, the collector reads log lines whose fields are in a different
// order, every line fails the field-count check or decodes into the wrong
// columns, and the scan reports that the account is clean. A customer would
// have no way to notice, and neither would we.
func TestTerraformFormatMatchesParser(t *testing.T) {
	raw, err := os.ReadFile("../../deploy/terraform/flowlogs.tf")
	if err != nil {
		t.Skipf("terraform module not present: %v", err)
	}

	// Extract the quoted "$${field}" entries from the log_format local.
	block := regexp.MustCompile(`(?s)log_format\s*=\s*join\(" ",\s*\[(.*?)\]\)`).
		FindSubmatch(raw)
	if block == nil {
		t.Fatal("could not find the log_format local in flowlogs.tf")
	}

	fields := regexp.MustCompile(`"\$\$\{([a-z0-9-]+)\}"`).
		FindAllStringSubmatch(string(block[1]), -1)
	if len(fields) == 0 {
		t.Fatal("no fields extracted from the terraform log_format")
	}

	terraform := make([]string, 0, len(fields))
	for _, m := range fields {
		terraform = append(terraform, m[1])
	}

	parser := regexp.MustCompile(`\$\{([a-z0-9-]+)\}`).FindAllStringSubmatch(LogFormat, -1)
	expected := make([]string, 0, len(parser))
	for _, m := range parser {
		expected = append(expected, m[1])
	}

	if strings.Join(terraform, " ") != strings.Join(expected, " ") {
		t.Fatalf("flow log format drift.\n terraform: %v\n    parser: %v", terraform, expected)
	}
	if len(expected) != fieldCount {
		t.Fatalf("parser expects %d fields but the format names %d", fieldCount, len(expected))
	}
}
