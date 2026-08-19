package ingest

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

// A real ALB access log line, with the sensitive parts filled in exactly as
// they would appear. The point of this fixture is that it is rich: if any of
// it survives parsing, SEC-18 is broken.
const albLine = `https 2026-08-10T12:15:30.123456Z app/prod-alb/abc123 ` +
	`203.0.113.44:51234 10.0.20.11:8080 0.001 0.412 0.000 200 200 1247 8931 ` +
	`"POST https://api.acme.com:443/v1/chat?user_email=alice@acme.com&token=sk-live-9f2 HTTP/2.0" ` +
	`"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/141.0" ` +
	`ECDHE-RSA-AES128-GCM-SHA256 TLSv1.3 ` +
	`arn:aws:elasticloadbalancing:us-east-1:1:targetgroup/prod/abc ` +
	`"Root=1-63f1a2b3-4c5d6e7f8a9b0c1d2e3f4a5b" "api.acme.com" "arn:aws:acm:..." ` +
	`0 2026-08-10T12:15:30.100000Z "forward" "-" "-" "10.0.20.11:8080" "200" "-" "-"`

func albWindow() awsread.Window {
	return awsread.Window{
		Start: time.Date(2026, 8, 10, 12, 0, 0, 0, time.UTC),
		End:   time.Date(2026, 8, 10, 13, 0, 0, 0, time.UTC),
	}
}

func TestExtractsTimingTargetAndByteCounts(t *testing.T) {
	got, err := ParseAccessLog(strings.NewReader(albLine), albWindow())
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d requests", len(got))
	}
	r := got[0]
	if r.Target != "10.0.20.11" {
		t.Errorf("target wrong: %q", r.Target)
	}
	if r.ReceivedBytes != 1247 || r.SentBytes != 8931 {
		t.Errorf("byte counts wrong: %+v", r)
	}
	if r.At.Minute() != 15 || r.At.Second() != 30 {
		t.Errorf("timestamp wrong: %v", r.At)
	}
}

// TestSEC18NothingSensitiveSurvivesParsing is the test this file exists for.
//
// An ALB line carries the request URL, the query string (here containing an
// email address and an API key), the user agent, the client IP, TLS details,
// and a trace ID. None of it may reach a wire type. Proving that by
// construction beats a comment claiming it.
func TestSEC18NothingSensitiveSurvivesParsing(t *testing.T) {
	got, err := ParseAccessLog(strings.NewReader(albLine), albWindow())
	if err != nil || len(got) != 1 {
		t.Fatalf("parse failed: %v", err)
	}

	// Render every field of the parsed record and confirm none of the
	// sensitive substrings appear anywhere in it.
	rendered := strings.Join([]string{
		got[0].At.String(), got[0].Target,
		time.Duration(got[0].ReceivedBytes).String(),
		time.Duration(got[0].SentBytes).String(),
	}, " ")

	for _, secret := range []string{
		"alice@acme.com",  // user identity
		"sk-live-9f2",     // credential in the query string
		"203.0.113.44",    // client address
		"Mozilla",         // user agent
		"Chrome",          // user agent
		"/v1/chat",        // request path
		"Root=1-63f1a2b3", // trace identifier
		"TLSv1.3",         // TLS details
		"api.acme.com",    // requested host
	} {
		if strings.Contains(rendered, secret) {
			t.Errorf("SEC-18: %q survived parsing into the wire record", secret)
		}
	}
}

func TestRequestsOutsideTheWindowAreDropped(t *testing.T) {
	early := strings.Replace(albLine, "2026-08-10T12:15:30.123456Z",
		"2026-08-10T09:15:30.123456Z", 1)
	got, err := ParseAccessLog(strings.NewReader(early), albWindow())
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("out-of-window request kept: %+v", got)
	}
}

// TestUnroutedRequestsAreDropped: a request that never reached a target cannot
// have caused a model call, and counting it would understate decoupling.
func TestUnroutedRequestsAreDropped(t *testing.T) {
	unrouted := strings.Replace(albLine, "10.0.20.11:8080 0.001", "- 0.001", 1)
	got, err := ParseAccessLog(strings.NewReader(unrouted), albWindow())
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("unrouted request kept: %+v", got)
	}
}

func TestMalformedLinesAreSkipped(t *testing.T) {
	input := strings.Join([]string{albLine, "garbage", "", albLine}, "\n")
	got, err := ParseAccessLog(strings.NewReader(input), albWindow())
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d requests", len(got))
	}
}

// TestExtraTrailingFieldsAreTolerated: AWS appends fields to this format over
// time, and a strict field count would break the collector on every release.
func TestExtraTrailingFieldsAreTolerated(t *testing.T) {
	future := albLine + ` "some-new-field-aws-added" "and-another"`
	got, err := ParseAccessLog(strings.NewReader(future), albWindow())
	if err != nil || len(got) != 1 {
		t.Fatalf("future format rejected: %v %d", err, len(got))
	}
}

func TestReadsGzippedAccessLogsFromS3(t *testing.T) {
	w := albWindow()
	api := &fakeS3{objects: map[string][]byte{
		"alb/AWSLogs/1/elasticloadbalancing/us-east-1/2026/08/10/x.log.gz": gzipped(albLine, albLine),
	}}
	r := &AccessLogReader{API: api, Bucket: "b", Prefix: "alb/AWSLogs/1/elasticloadbalancing/us-east-1"}

	got, err := r.Read(context.Background(), w)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d requests", len(got))
	}
}

func TestCorruptAccessLogObjectDoesNotAbortTheRun(t *testing.T) {
	w := albWindow()
	api := &fakeS3{objects: map[string][]byte{
		"alb/2026/08/10/bad.log.gz":  []byte("not gzip"),
		"alb/2026/08/10/good.log.gz": gzipped(albLine),
	}}
	r := &AccessLogReader{API: api, Bucket: "b", Prefix: "alb"}
	got, err := r.Read(context.Background(), w)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("one bad object should not lose the good one: got %d", len(got))
	}
}

func TestAccessLogInvalidWindowIsRefused(t *testing.T) {
	r := &AccessLogReader{API: &fakeS3{}, Bucket: "b"}
	if _, err := r.Read(context.Background(), awsread.Window{}); err == nil {
		t.Fatal("a zero window must be refused")
	}
}
