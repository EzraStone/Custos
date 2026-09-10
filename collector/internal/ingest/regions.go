package ingest

import (
	"context"
	"fmt"
	"sort"

	"github.com/aws/aws-sdk-go-v2/service/ec2"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

// Which regions this account actually uses.
//
// The collector reads one region at a time, and a scan of us-east-1 reports "no
// unsanctioned agents" about eu-west-1 with exactly the same confidence it
// reports it about the region it read. That is the same failure as an agent
// behind an undeclared gateway, one level up: the finding is absent because
// nobody looked, and nothing says so.
//
// Enabled regions are not the answer on their own. AWS enables about seventeen
// by default and almost every account uses two or three, so a list of enabled
// regions is a list nobody reads. A region containing a flow log is a region
// somebody configured on purpose.

// Region is one region and what it holds.
type Region struct {
	Name     string
	FlowLogs int
	// Err is set when the region could not be asked. Reported rather than
	// treated as zero: "we could not check eu-west-1" and "eu-west-1 has no
	// flow logs" are different sentences and only one of them is reassuring.
	Err error
}

// Survey asks every enabled region whether it has flow logs.
type Survey struct {
	Regions awsread.RegionAPI

	// FlowLogsIn returns a client bound to one region. A factory rather than a
	// client, because DescribeFlowLogs is answered by the region it is sent
	// to — asking us-east-1 about eu-west-1 returns nothing at all, which
	// would read as "no flow logs there" and be wrong every time.
	FlowLogsIn func(region string) awsread.FlowLogAPI
}

// Run lists enabled regions and counts the flow logs in each.
//
// A region that cannot be asked appears with its error rather than being
// dropped. Dropping it would turn a permissions problem into a shorter list,
// which reads as good news.
func (s *Survey) Run(ctx context.Context) ([]Region, error) {
	if s.Regions == nil || s.FlowLogsIn == nil {
		return nil, fmt.Errorf("region survey needs both a region lister and a per-region client")
	}

	out, err := s.Regions.DescribeRegions(ctx, &ec2.DescribeRegionsInput{})
	if err != nil {
		return nil, fmt.Errorf("listing regions: %w", err)
	}

	var found []Region
	for _, r := range out.Regions {
		if r.RegionName == nil {
			continue
		}
		found = append(found, s.count(ctx, *r.RegionName))
	}
	sort.Slice(found, func(i, j int) bool { return found[i].Name < found[j].Name })
	return found, nil
}

func (s *Survey) count(ctx context.Context, region string) Region {
	client := s.FlowLogsIn(region)
	if client == nil {
		return Region{Name: region, Err: fmt.Errorf("no client for %s", region)}
	}
	logs, err := client.DescribeFlowLogs(ctx, &ec2.DescribeFlowLogsInput{})
	if err != nil {
		return Region{Name: region, Err: err}
	}
	return Region{Name: region, FlowLogs: len(logs.FlowLogs)}
}

// Configured returns the regions with at least one flow log.
//
// The list to put in front of a customer: every one of these is a region
// somebody set up deliberately, and every one the collector is not reading is
// a region this scan says nothing about.
func Configured(regions []Region) []string {
	var out []string
	for _, r := range regions {
		if r.Err == nil && r.FlowLogs > 0 {
			out = append(out, r.Name)
		}
	}
	return out
}

// Unreachable returns the regions that could not be asked.
func Unreachable(regions []Region) []string {
	var out []string
	for _, r := range regions {
		if r.Err != nil {
			out = append(out, r.Name)
		}
	}
	return out
}
