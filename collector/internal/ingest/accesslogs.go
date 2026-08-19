package ingest

import (
	"bufio"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// Load balancer access logs carry the single strongest classifier signal:
// whether a burst of model traffic was answering something a human asked for.
// Measured on the A0 corpus, having them lifts recall from 60% to 100%, and the
// agents recovered are the low-volume ones — which are usually the ones a
// customer would most want to know about.
//
// They are also the most sensitive thing the collector touches, and this file
// is where SEC-18 earns its keep. An ALB access log line contains the request
// URL, the query string, the user agent, the client IP and port, TLS details,
// and trace identifiers. All of it describes the people using the system.
//
// Four fields are extracted: when the request arrived, which target handled it,
// and the two byte counts. Everything else is discarded at parse time and has
// nowhere to go afterwards, because wire.InboundRequest has no field that could
// hold it. Correlation needs nothing more, so nothing more is taken.

// Field positions in the ALB access log format. AWS appends new fields at the
// end over time, so indexing from the front stays valid across versions while a
// field count check would break on every AWS release.
//
//	0  type                       6  target_processing_time
//	1  time                       7  response_processing_time
//	2  elb                        8  elb_status_code
//	3  client:port                9  target_status_code
//	4  target:port               10  received_bytes
//	5  request_processing_time   11  sent_bytes
//	12 "request"  and everything after it, none of which is read
const (
	albFieldTime          = 1
	albFieldTarget        = 4
	albFieldReceivedBytes = 10
	albFieldSentBytes     = 11
	albMinFields          = 12
)

// AccessLogReader reads ALB access logs from S3.
type AccessLogReader struct {
	API    awsread.ObjectAPI
	Bucket string
	Prefix string
}

// Read collects inbound request records for the window.
func (r *AccessLogReader) Read(ctx context.Context, w awsread.Window) ([]wire.InboundRequest, error) {
	if !w.Valid() {
		return nil, fmt.Errorf("invalid window %v..%v", w.Start, w.End)
	}

	var out []wire.InboundRequest
	for _, prefix := range r.dayPrefixes(w) {
		var token *string
		for {
			list, err := r.API.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
				Bucket:            aws.String(r.Bucket),
				Prefix:            aws.String(prefix),
				ContinuationToken: token,
			})
			if err != nil {
				return out, fmt.Errorf("listing s3://%s/%s: %w", r.Bucket, prefix, err)
			}

			for _, obj := range list.Contents {
				key := aws.ToString(obj.Key)
				if !strings.HasSuffix(key, ".log.gz") {
					continue
				}
				requests, err := r.readObject(ctx, key, w)
				if err != nil {
					// One unreadable object degrades recall slightly. Aborting
					// would degrade it entirely.
					continue
				}
				out = append(out, requests...)
			}

			if list.NextContinuationToken == nil || *list.NextContinuationToken == "" {
				break
			}
			token = list.NextContinuationToken

			if err := ctx.Err(); err != nil {
				return out, err
			}
		}
	}
	return out, nil
}

func (r *AccessLogReader) dayPrefixes(w awsread.Window) []string {
	base := strings.TrimSuffix(r.Prefix, "/")
	var out []string
	day := w.Start.UTC().AddDate(0, 0, -1).Truncate(24 * time.Hour)
	last := w.End.UTC().Truncate(24 * time.Hour)
	for !day.After(last) {
		out = append(out, fmt.Sprintf("%s/%04d/%02d/%02d/",
			base, day.Year(), day.Month(), day.Day()))
		day = day.AddDate(0, 0, 1)
	}
	return out
}

func (r *AccessLogReader) readObject(ctx context.Context, key string, w awsread.Window) ([]wire.InboundRequest, error) {
	obj, err := r.API.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(r.Bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, err
	}
	defer obj.Body.Close()

	gz, err := gzip.NewReader(io.LimitReader(obj.Body, MaxObjectBytes))
	if err != nil {
		return nil, fmt.Errorf("decompressing %s: %w", key, err)
	}
	defer gz.Close()

	return ParseAccessLog(io.LimitReader(gz, MaxObjectBytes), w)
}

// ParseAccessLog extracts inbound requests from ALB access log lines.
//
// Exported so that the discarding is directly testable. A test that proves a
// URL and user agent cannot survive parsing is worth more than a comment saying
// they are not collected.
func ParseAccessLog(r io.Reader, w awsread.Window) ([]wire.InboundRequest, error) {
	var out []wire.InboundRequest
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		request, ok := parseAccessLogLine(scanner.Text())
		if !ok {
			continue
		}
		if request.At.Before(w.Start) || !request.At.Before(w.End) {
			continue
		}
		out = append(out, request)
	}
	return out, scanner.Err()
}

func parseAccessLogLine(line string) (wire.InboundRequest, bool) {
	// Split on spaces only. Quoted fields — the request line, the user agent —
	// may contain spaces and would be mangled by this, which does not matter:
	// every field this function reads sits before the first quoted one, and
	// nothing after it is ever looked at.
	fields := strings.Fields(line)
	if len(fields) < albMinFields {
		return wire.InboundRequest{}, false
	}

	at, err := time.Parse(time.RFC3339Nano, fields[albFieldTime])
	if err != nil {
		return wire.InboundRequest{}, false
	}

	// Strip the port: the classifier correlates on target address, and the
	// ephemeral port would fragment the grouping.
	target := fields[albFieldTarget]
	if host, _, found := strings.Cut(target, ":"); found {
		target = host
	}
	if target == "-" {
		// No target was reached, so no workload made model calls for it.
		return wire.InboundRequest{}, false
	}

	received, err1 := strconv.ParseInt(fields[albFieldReceivedBytes], 10, 64)
	sent, err2 := strconv.ParseInt(fields[albFieldSentBytes], 10, 64)
	if err1 != nil || err2 != nil {
		return wire.InboundRequest{}, false
	}

	return wire.InboundRequest{
		At:            at.UTC(),
		Target:        target,
		ReceivedBytes: received,
		SentBytes:     sent,
	}, true
}
