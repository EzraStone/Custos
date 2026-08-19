package ingest

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	s3types "github.com/aws/aws-sdk-go-v2/service/s3/types"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

type fakeS3 struct {
	objects map[string][]byte
	listed  []string
}

func (f *fakeS3) ListObjectsV2(_ context.Context, in *s3.ListObjectsV2Input,
	_ ...func(*s3.Options)) (*s3.ListObjectsV2Output, error) {
	prefix := aws.ToString(in.Prefix)
	f.listed = append(f.listed, prefix)
	out := &s3.ListObjectsV2Output{}
	for key := range f.objects {
		if strings.HasPrefix(key, prefix) {
			out.Contents = append(out.Contents, s3types.Object{Key: aws.String(key)})
		}
	}
	return out, nil
}

func (f *fakeS3) GetObject(_ context.Context, in *s3.GetObjectInput,
	_ ...func(*s3.Options)) (*s3.GetObjectOutput, error) {
	body, ok := f.objects[aws.ToString(in.Key)]
	if !ok {
		return nil, io.ErrUnexpectedEOF
	}
	return &s3.GetObjectOutput{Body: io.NopCloser(bytes.NewReader(body))}, nil
}

func gzipped(lines ...string) []byte {
	var buf bytes.Buffer
	w := gzip.NewWriter(&buf)
	_, _ = w.Write([]byte(strings.Join(lines, "\n") + "\n"))
	_ = w.Close()
	return buf.Bytes()
}

// lineAt builds a flow log line whose record timestamp is t.
func lineAt(t time.Time) string {
	f := strings.Fields(line)
	f[10] = strconv.FormatInt(t.Unix(), 10)
	f[11] = strconv.FormatInt(t.Unix()+60, 10)
	return strings.Join(f, " ")
}

func s3Window() awsread.Window {
	return awsread.Window{
		Start: time.Date(2026, 8, 10, 12, 0, 0, 0, time.UTC),
		End:   time.Date(2026, 8, 10, 13, 0, 0, 0, time.UTC),
	}
}

func TestReadsGzippedObjectsUnderDayPrefixes(t *testing.T) {
	w := s3Window()
	inside := lineAt(w.Start.Add(10 * time.Minute))
	api := &fakeS3{objects: map[string][]byte{
		"AWSLogs/1/vpcflowlogs/us-east-1/2026/08/10/a.log.gz": gzipped(inside, inside),
	}}
	r := &S3Reader{API: api, Bucket: "b", AccountID: "1", Region: "us-east-1"}

	records, stats, err := r.Read(context.Background(), w)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 || stats.Parsed != 2 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}
}

// TestListsThePreviousDayToo: an object written just after midnight can hold
// records from before it, and missing them silently shrinks the window.
func TestListsThePreviousDayToo(t *testing.T) {
	api := &fakeS3{objects: map[string][]byte{}}
	r := &S3Reader{API: api, Bucket: "b", AccountID: "1", Region: "us-east-1"}
	if _, _, err := r.Read(context.Background(), s3Window()); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(api.listed, " ")
	if !strings.Contains(joined, "2026/08/09/") {
		t.Fatalf("previous day not listed: %v", api.listed)
	}
	if !strings.Contains(joined, "2026/08/10/") {
		t.Fatalf("target day not listed: %v", api.listed)
	}
}

// TestWindowIsAppliedToRecordsNotFilenames: the object name timestamp is when
// delivery started, not what is inside it.
func TestWindowIsAppliedToRecordsNotFilenames(t *testing.T) {
	w := s3Window()
	inside := lineAt(w.Start.Add(5 * time.Minute))
	outside := lineAt(w.End.Add(2 * time.Hour))
	api := &fakeS3{objects: map[string][]byte{
		"AWSLogs/1/vpcflowlogs/us-east-1/2026/08/10/a.log.gz": gzipped(inside, outside, inside),
	}}
	r := &S3Reader{API: api, Bucket: "b", AccountID: "1", Region: "us-east-1"}

	records, _, err := r.Read(context.Background(), w)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 {
		t.Fatalf("expected the out-of-window record to be dropped, got %d", len(records))
	}
}

func TestNonGzipKeysAreIgnored(t *testing.T) {
	w := s3Window()
	api := &fakeS3{objects: map[string][]byte{
		"AWSLogs/1/vpcflowlogs/us-east-1/2026/08/10/README.txt": []byte("not a flow log"),
	}}
	r := &S3Reader{API: api, Bucket: "b", AccountID: "1", Region: "us-east-1"}
	records, stats, err := r.Read(context.Background(), w)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 || stats.Malformed != 0 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}
}

func TestCorruptObjectDoesNotAbortTheRun(t *testing.T) {
	w := s3Window()
	inside := lineAt(w.Start.Add(time.Minute))
	api := &fakeS3{objects: map[string][]byte{
		"AWSLogs/1/vpcflowlogs/us-east-1/2026/08/10/bad.log.gz":  []byte("not gzip"),
		"AWSLogs/1/vpcflowlogs/us-east-1/2026/08/10/good.log.gz": gzipped(inside),
	}}
	r := &S3Reader{API: api, Bucket: "b", AccountID: "1", Region: "us-east-1"}

	records, stats, err := r.Read(context.Background(), w)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 || stats.Malformed != 1 {
		t.Fatalf("got %d records, stats %+v", len(records), stats)
	}
}

func TestExplicitPrefixOverridesTheDefault(t *testing.T) {
	api := &fakeS3{objects: map[string][]byte{}}
	r := &S3Reader{API: api, Bucket: "b", Prefix: "custom/path/", AccountID: "1", Region: "us-east-1"}
	if _, _, err := r.Read(context.Background(), s3Window()); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(api.listed[0], "custom/path/") {
		t.Fatalf("explicit prefix ignored: %v", api.listed)
	}
}

func TestS3InvalidWindowIsRefused(t *testing.T) {
	r := &S3Reader{API: &fakeS3{}, Bucket: "b"}
	if _, _, err := r.Read(context.Background(), awsread.Window{}); err == nil {
		t.Fatal("a zero window must be refused")
	}
}
