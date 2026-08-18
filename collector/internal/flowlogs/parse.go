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
	"${pkt-dst-aws-service} ${tcp-flags}"

const fieldCount = 19

// Stats describes what a parse run saw. Reported to the customer and carried
// into the scan report, because coverage is part of a finding's meaning.
type Stats struct {
	Lines     int
	Parsed    int
	Skipped   int
	NoData    int
	SkipData  int
	Malformed int
}

// Coverage is the fraction of lines successfully parsed.
func (s Stats) Coverage() float64 {
	if s.Lines == 0 {
		return 0
	}
	return float64(s.Parsed) / float64(s.Lines)
}

// Parse reads flow log lines and returns wire records plus statistics.
func Parse(r io.Reader) ([]wire.FlowRecord, Stats, error) {
	var (
		out     []wire.FlowRecord
		stats   Stats
		scanner = bufio.NewScanner(r)
	)
	// Flow log lines are short, but a corrupted stream can produce a very long
	// one; cap the buffer rather than letting it grow without bound.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "version ") {
			continue // blank, or the header some exports include
		}
		stats.Lines++

		record, status, err := parseLine(line)
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

func parseLine(line string) (wire.FlowRecord, string, error) {
	f := strings.Fields(line)
	if len(f) != fieldCount {
		return wire.FlowRecord{}, "", errFieldCount
	}
	if f[13] != "OK" {
		return wire.FlowRecord{}, f[13], nil
	}

	ints := make([]int64, 0, 8)
	for _, idx := range []int{5, 6, 7, 8, 9, 10, 11, 18} {
		v, err := strconv.ParseInt(f[idx], 10, 64)
		if err != nil {
			return wire.FlowRecord{}, "", err
		}
		ints = append(ints, v)
	}

	service := f[17]
	if service == "-" {
		service = ""
	}

	return wire.FlowRecord{
		AccountID:     f[1],
		InterfaceID:   f[2],
		SrcAddr:       f[3],
		DstAddr:       f[4],
		SrcPort:       int(ints[0]),
		DstPort:       int(ints[1]),
		Protocol:      int(ints[2]),
		Packets:       ints[3],
		Bytes:         ints[4],
		Start:         time.Unix(ints[5], 0).UTC(),
		End:           time.Unix(ints[6], 0).UTC(),
		Action:        f[12],
		LogStatus:     f[13],
		VpcID:         f[14],
		SubnetID:      f[15],
		Direction:     wire.Direction(f[16]),
		DstAWSService: service,
		TCPFlags:      int(ints[7]),
	}, "OK", nil
}

type parseError string

func (e parseError) Error() string { return string(e) }

const errFieldCount = parseError("flow log line does not have 19 fields")
