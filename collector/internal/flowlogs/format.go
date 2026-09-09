package flowlogs

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Format describes where each field sits in a flow log line.
//
// Custos ships a Terraform module that creates a flow log in its own format,
// and that remains the best case. It is not the common case. A platform team
// with flow logs already going to an S3 log archive is being asked to pay
// twice — VPC Flow Logs bill per gigabyte ingested, and a large account's
// second copy is a real line item — and to get a second Terraform apply
// through change control. Both are reasons to say no that have nothing to do
// with whether the product works.
//
// So the parser reads whatever format the account already has. Fields are
// located by name rather than by position, missing ones degrade the scan in
// stated ways rather than failing it, and the six that cannot be worked around
// are named as required.
//
// Unknown field names are kept in place and ignored. AWS adds fields to the
// flow log schema periodically, and a parser that refused a format containing
// one it had not heard of would break on a customer who simply configured
// theirs after we shipped.
type Format struct {
	names []string
	index map[string]int
}

// Required names the fields without which a scan cannot mean anything.
//
//	interface-id  attribution: which workload this traffic belongs to
//	srcaddr       what it talked to, and which end is which
//	dstaddr       the same
//	bytes         the entire classifier signal is byte asymmetry
//	start, end    which window a record falls in
//
// Everything else degrades. These do not: without one of them the scan is not
// a worse scan, it is a different product.
var Required = []string{
	"interface-id", "srcaddr", "dstaddr", "bytes", "start", "end",
}

// optional maps a field Custos uses to what is lost without it, phrased for
// the person deciding whether to reconfigure their flow log.
var optional = map[string]string{
	"dstport": "destination ports are unknown, so MCP servers and datastores " +
		"cannot be told apart from any other internal service",
	"flow-direction": "direction is inferred from the interface's own address " +
		"instead of read, which is correct for ordinary traffic and wrong for " +
		"an interface serving several addresses",
	"pkt-dst-aws-service": "AWS service destinations are unnamed, so Bedrock " +
		"and S3 traffic is classified from address ranges alone",
	"pkt-src-aws-service": "the AWS service at the source end is unnamed, so " +
		"the return leg of an AWS conversation arrives unattributed",
	"log-status": "OK is assumed, so AWS's own NODATA and SKIPDATA markers " +
		"cannot be counted and coverage is overstated",
	"account-id": "the account is taken from configuration rather than from " +
		"the record",
}

// LogFormatHeader is LogFormat as AWS writes it at the top of an S3 object:
// the same fields, named rather than templated.
var LogFormatHeader = strings.Join(MustParseFormat(LogFormat).Fields(), " ")

var fieldName = regexp.MustCompile(`^[a-z][a-z0-9-]*$`)

var formatToken = regexp.MustCompile(`\$\{([^}]*)\}`)

// ParseFormat reads a flow log format specification.
//
// Both spellings are accepted, because both are what a customer has to hand:
// the `${field}` form they typed into the console or Terraform, and the bare
// space-separated form AWS writes as the header line of an S3 object.
func ParseFormat(spec string) (Format, error) {
	spec = strings.TrimSpace(spec)
	if spec == "" {
		return Format{}, fmt.Errorf("empty flow log format")
	}

	var names []string
	if strings.Contains(spec, "${") {
		for _, m := range formatToken.FindAllStringSubmatch(spec, -1) {
			names = append(names, strings.TrimSpace(m[1]))
		}
		if len(names) == 0 {
			return Format{}, fmt.Errorf("no ${field} tokens in %q", spec)
		}
	} else {
		names = strings.Fields(spec)
	}

	index := make(map[string]int, len(names))
	for i, name := range names {
		if !fieldName.MatchString(name) {
			return Format{}, fmt.Errorf("field %d is not a flow log field name: %q", i+1, name)
		}
		if _, seen := index[name]; seen {
			// Not pedantry. A duplicated name means one of the two positions
			// is being silently ignored, and which one depends on iteration
			// order rather than on anything the customer intended.
			return Format{}, fmt.Errorf("field %q appears twice", name)
		}
		index[name] = i
	}
	return Format{names: names, index: index}, nil
}

// MustParseFormat is ParseFormat for formats compiled into the binary.
func MustParseFormat(spec string) Format {
	f, err := ParseFormat(spec)
	if err != nil {
		panic(err)
	}
	return f
}

// Fields returns the field names in the order they appear on a line.
func (f Format) Fields() []string { return append([]string(nil), f.names...) }

// Count is how many fields a line in this format has.
func (f Format) Count() int { return len(f.names) }

// Has reports whether the format carries a field.
func (f Format) Has(name string) bool {
	_, ok := f.index[name]
	return ok
}

// Missing returns the required fields this format does not carry.
func (f Format) Missing() []string {
	var out []string
	for _, name := range Required {
		if !f.Has(name) {
			out = append(out, name)
		}
	}
	return out
}

// Usable reports whether a scan built from this format can mean anything.
func (f Format) Usable() bool { return len(f.Missing()) == 0 }

// Degradations names what is lost, alphabetically by field, so the same
// format always reports the same list in the same order.
//
// Reported rather than inferred from a thin report later. A customer whose
// format omits the port fields should be told that before the scan, not left
// to wonder why nothing was identified as an MCP server.
func (f Format) Degradations() []string {
	names := make([]string, 0, len(optional))
	for name := range optional {
		names = append(names, name)
	}
	sort.Strings(names)

	var out []string
	for _, name := range names {
		if !f.Has(name) {
			out = append(out, fmt.Sprintf("%s absent: %s", name, optional[name]))
		}
	}
	return out
}

// field returns the value at a named position, or "" when the format does not
// carry it. Callers treat "" and AWS's own "-" the same way.
func (f Format) field(fields []string, name string) string {
	i, ok := f.index[name]
	if !ok || i >= len(fields) {
		return ""
	}
	if v := fields[i]; v != "-" {
		return v
	}
	return ""
}

// IsHeader reports whether a line is the field-name header AWS writes as the
// first line of a flow log object delivered to S3.
//
// The test is that every token is shaped like a field name: lower case,
// starting with a letter. A record cannot be, because `bytes` and the two
// timestamps are always numeric and `log-status` is upper case. Matching on a
// "version " prefix instead would misread the first record of a format whose
// first field happens to be a lower-case identifier.
func IsHeader(line string) bool {
	fields := strings.Fields(line)
	if len(fields) < 2 {
		return false
	}
	for _, token := range fields {
		if !fieldName.MatchString(token) {
			return false
		}
	}
	return true
}
