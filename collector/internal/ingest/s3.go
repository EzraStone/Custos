package ingest

import (
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/flowlogs"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// S3 delivery is the cheaper of the two flow log destinations, so a
// cost-conscious platform team — which is the target profile — is more likely
// to have this than CloudWatch Logs. Supporting it is not an optional extra.
//
// Objects arrive gzipped under a date-partitioned prefix:
//
//	AWSLogs/<account>/vpcflowlogs/<region>/<yyyy>/<mm>/<dd>/<file>.log.gz
//
// The date partitioning matters. Listing the whole bucket to find one hour of
// logs would be slow and would bill the customer for tens of thousands of LIST
// requests; deriving the day prefixes from the window keeps it to a handful.

// MaxObjectBytes caps a single decompressed object. Flow log objects are
// typically a few megabytes; anything wildly larger is a signal something is
// wrong, and streaming it into memory would take the collector down inside the
// customer's account.
const MaxObjectBytes = 512 << 20

// S3Reader reads VPC Flow Logs delivered to an S3 bucket.
type S3Reader struct {
	API       awsread.ObjectAPI
	Bucket    string
	Prefix    string
	AccountID string
	Region    string
	MaxEvents int
}

// dayPrefixes returns the S3 prefixes covering a window, one per UTC day.
func (r *S3Reader) dayPrefixes(w awsread.Window) []string {
	base := strings.TrimSuffix(r.Prefix, "/")
	if base == "" {
		base = fmt.Sprintf("AWSLogs/%s/vpcflowlogs/%s", r.AccountID, r.Region)
	}

	var out []string
	// Start a day early: an object written just after midnight can contain
	// records from before it, and missing them would silently shrink the window.
	day := w.Start.UTC().AddDate(0, 0, -1).Truncate(24 * time.Hour)
	last := w.End.UTC().Truncate(24 * time.Hour)
	for !day.After(last) {
		out = append(out, fmt.Sprintf("%s/%04d/%02d/%02d/",
			base, day.Year(), day.Month(), day.Day()))
		day = day.AddDate(0, 0, 1)
	}
	return out
}

// Read collects flow records for the window.
func (r *S3Reader) Read(ctx context.Context, w awsread.Window) ([]wire.FlowRecord, flowlogs.Stats, error) {
	if !w.Valid() {
		return nil, flowlogs.Stats{}, fmt.Errorf("invalid window %v..%v", w.Start, w.End)
	}
	limit := r.MaxEvents
	if limit <= 0 {
		limit = MaxEventsPerRun
	}

	var (
		records []wire.FlowRecord
		stats   flowlogs.Stats
	)

	for _, prefix := range r.dayPrefixes(w) {
		var token *string
		for {
			out, err := r.API.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
				Bucket:            aws.String(r.Bucket),
				Prefix:            aws.String(prefix),
				ContinuationToken: token,
			})
			if err != nil {
				return records, stats, fmt.Errorf("listing s3://%s/%s: %w", r.Bucket, prefix, err)
			}

			for _, obj := range out.Contents {
				if obj.Key == nil || !strings.HasSuffix(*obj.Key, ".gz") {
					continue
				}
				// Objects outside the window still get read: the file name
				// timestamp is when delivery started, not what is inside, and
				// filtering on it drops records. The window is applied to
				// record timestamps instead.
				batch, batchStats, err := r.readObject(ctx, *obj.Key, w)
				if err != nil {
					stats.Malformed++
					continue
				}
				records = append(records, batch...)
				stats = mergeStats(stats, batchStats)

				if len(records) >= limit {
					stats.Truncated = true
					return records, stats, nil
				}
			}

			if out.NextContinuationToken == nil || *out.NextContinuationToken == "" {
				break
			}
			token = out.NextContinuationToken

			if err := ctx.Err(); err != nil {
				return records, stats, err
			}
		}
	}

	return records, stats, nil
}

func (r *S3Reader) readObject(ctx context.Context, key string, w awsread.Window) ([]wire.FlowRecord, flowlogs.Stats, error) {
	out, err := r.API.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(r.Bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, flowlogs.Stats{}, err
	}
	defer out.Body.Close()

	gz, err := gzip.NewReader(io.LimitReader(out.Body, MaxObjectBytes))
	if err != nil {
		return nil, flowlogs.Stats{}, fmt.Errorf("decompressing %s: %w", key, err)
	}
	defer gz.Close()

	records, stats, err := flowlogs.Parse(io.LimitReader(gz, MaxObjectBytes))
	if err != nil {
		return records, stats, err
	}

	// Apply the window to record timestamps rather than to object names.
	kept := records[:0]
	for _, rec := range records {
		if !rec.Start.Before(w.Start) && rec.Start.Before(w.End) {
			kept = append(kept, rec)
		}
	}
	stats.Parsed = len(kept)
	return kept, stats, nil
}
