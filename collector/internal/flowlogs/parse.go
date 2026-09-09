// Package flowlogs parses VPC Flow Logs v5 lines into wire records.
//
// The parser is strict about field count and lenient about everything else.
// Real log groups contain SKIPDATA and NODATA lines, records for interfaces
// that no longer exist, and occasional truncation. A collector that halts on
// the first malformed line is a collector that a customer disables in week two,
// so bad lines are counted and skipped rather than fatal — but the count is
// reported, because silently dropping a third of an account's traffic and
// declaring the scan clean would be far worse than failing loudly.
package flowlogs

import (
	"bufio"
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

// LogFormat is the flow log format Custos requires. Shipped verbatim in the
// Terraform module so the two cannot drift.
const LogFormat = "${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} " +
	"${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} " +
	"${action} ${log-status} ${vpc-id} ${subnet-id} ${flow-direction} " +
	"${pkt-src-aws-service} ${pkt-dst-aws-service} ${tcp-flags}"

const fieldCount = 20

// Stats describes what a parse run saw. Reported to the customer and carried
// into the scan report, because coverage is part of a finding's meaning.
type Stats struct {
	Lines     int
	Parsed    int
	Skipped   int
	NoData    int
	SkipData  int
	Malformed int

	// Truncated is set when a read stopped at its event limit rather than at
	// the end of the window. A truncated window changes what an absence of
	// findings means, so it is carried all the way into the report.
	Truncated bool
}

// Coverage is the fraction of lines successfully parsed.
func (s Stats) Coverage() float64 {
	if s.Lines == 0 {
		return 0
	}
	return float64(s.Parsed) / float64(s.Lines)
}

// Default is the format Custos's own Terraform module configures.
var Default = MustParseFormat(LogFormat)

// Parse reads flow log lines in Custos's own format.
//
// Kept as the zero-argument spelling because it is what a caller reading a
// log group the Terraform module created wants, and because every existing
// caller and test means exactly this.
func Parse(r io.Reader) ([]wire.FlowRecord, Stats, error) {
	return ParseFormatted(r, Default)
}

// ParseFormatted reads flow log lines in a stated format.
//
// A header line inside the stream overrides the format argument for the rest
// of that stream. AWS writes one at the top of every object it delivers to S3,
// and believing the file over the configuration is right: the file is what was
// actually written, and a stale --flow-log-format setting is otherwise a
// silent misparse rather than an error.
func ParseFormatted(r io.Reader, format Format) ([]wire.FlowRecord, Stats, error) {
	var (
		out     []wire.FlowRecord
		stats   Stats
		scanner = bufio.NewScanner(r)
	)
	if !format.Usable() {
		return nil, stats, fmt.Errorf(
			"flow log format is missing %s", strings.Join(format.Missing(), ", "),
		)
	}
	// Flow log lines are short, but a corrupted stream can produce a very long
	// one; cap the buffer rather than letting it grow without bound.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if IsHeader(line) {
			// The file says what it contains. Trusting it over the caller's
			// argument turns a stale format setting into a correct parse
			// rather than a silent misread of every line.
			seen, err := ParseFormat(line)
			if err != nil {
				return nil, stats, fmt.Errorf("unreadable header line: %w", err)
			}
			if !seen.Usable() {
				return nil, stats, fmt.Errorf(
					"this log's format is missing %s",
					strings.Join(seen.Missing(), ", "),
				)
			}
			format = seen
			continue
		}
		stats.Lines++

		record, status, err := parseLine(line, format)
		switch {
		case err != nil:
			stats.Malformed++
			stats.Skipped++
		case status == "NODATA":
			stats.NoData++
			stats.Skipped++
		case status == "SKIPDATA":
			// AWS dropped records it could not capture. Counted prominently:
			// it means the account's traffic is under-represented, and a scan
			// with high SKIPDATA is a scan whose absence of findings means less.
			stats.SkipData++
			stats.Skipped++
		default:
			stats.Parsed++
			out = append(out, record)
		}
	}
	return out, stats, scanner.Err()
}

func parseLine(line string, format Format) (wire.FlowRecord, string, error) {
	f := strings.Fields(line)
	if len(f) != format.Count() {
		return wire.FlowRecord{}, "", errFieldCount
	}

	// A format without log-status cannot carry NODATA or SKIPDATA, so every
	// line it does carry is a record. Assuming OK is the only reading
	// available; that it overstates coverage is stated by Degradations.
	status := format.field(f, "log-status")
	if status == "" {
		status = "OK"
	}
	if status != "OK" {
		return wire.FlowRecord{}, status, nil
	}

	num := func(name string) (int64, error) {
		raw := format.field(f, name)
		if raw == "" {
			return 0, nil
		}
		return strconv.ParseInt(raw, 10, 64)
	}

	var err error
	var v [8]int64
	for i, name := range []string{
		"srcport", "dstport", "protocol", "packets", "bytes",
		"start", "end", "tcp-flags",
	} {
		if v[i], err = num(name); err != nil {
			return wire.FlowRecord{}, "", err
		}
	}

	return wire.FlowRecord{
		AccountID:     format.field(f, "account-id"),
		InterfaceID:   format.field(f, "interface-id"),
		SrcAddr:       format.field(f, "srcaddr"),
		DstAddr:       format.field(f, "dstaddr"),
		SrcPort:       int(v[0]),
		DstPort:       int(v[1]),
		Protocol:      int(v[2]),
		Packets:       v[3],
		Bytes:         v[4],
		Start:         time.Unix(v[5], 0).UTC(),
		End:           time.Unix(v[6], 0).UTC(),
		Action:        format.field(f, "action"),
		LogStatus:     status,
		VpcID:         format.field(f, "vpc-id"),
		SubnetID:      format.field(f, "subnet-id"),
		Direction:     wire.Direction(format.field(f, "flow-direction")),
		SrcAWSService: format.field(f, "pkt-src-aws-service"),
		DstAWSService: format.field(f, "pkt-dst-aws-service"),
		TCPFlags:      int(v[7]),
	}, "OK", nil
}

type parseError string

func (e parseError) Error() string { return string(e) }

const errFieldCount = parseError("flow log line does not have the field count its format names")
