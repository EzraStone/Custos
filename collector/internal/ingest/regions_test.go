package ingest

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

type fakeRegions struct {
	names []string
	err   error
}

func (f fakeRegions) DescribeRegions(context.Context, *ec2.DescribeRegionsInput,
	...func(*ec2.Options)) (*ec2.DescribeRegionsOutput, error) {
	if f.err != nil {
		return nil, f.err
	}
	out := &ec2.DescribeRegionsOutput{}
	for _, n := range f.names {
		out.Regions = append(out.Regions, ec2types.Region{RegionName: aws.String(n)})
	}
	return out, nil
}

type fakeFlowLogs struct {
	count int
	err   error
}

func (f fakeFlowLogs) DescribeFlowLogs(context.Context, *ec2.DescribeFlowLogsInput,
	...func(*ec2.Options)) (*ec2.DescribeFlowLogsOutput, error) {
	if f.err != nil {
		return nil, f.err
	}
	out := &ec2.DescribeFlowLogsOutput{}
	for i := 0; i < f.count; i++ {
		out.FlowLogs = append(out.FlowLogs, ec2types.FlowLog{})
	}
	return out, nil
}

func survey(names []string, per map[string]fakeFlowLogs) *Survey {
	return &Survey{
		Regions: fakeRegions{names: names},
		FlowLogsIn: func(region string) awsread.FlowLogAPI {
			f, ok := per[region]
			if !ok {
				return fakeFlowLogs{}
			}
			return f
		},
	}
}

func TestOnlyRegionsWithFlowLogsAreWorthNaming(t *testing.T) {
	// AWS enables about seventeen regions by default. A list of enabled
	// regions is a list nobody reads; a region with a flow log in it is one
	// somebody configured on purpose.
	found, err := survey(
		[]string{"us-east-1", "eu-west-1", "ap-south-1", "sa-east-1"},
		map[string]fakeFlowLogs{
			"us-east-1": {count: 2},
			"eu-west-1": {count: 1},
		},
	).Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if got := Configured(found); strings.Join(got, ",") != "eu-west-1,us-east-1" {
		t.Fatalf("configured regions: %v", got)
	}
}

func TestTheAnswerIsOrderedSoTwoRunsReadTheSame(t *testing.T) {
	found, _ := survey(
		[]string{"us-west-2", "af-south-1", "eu-west-1"},
		map[string]fakeFlowLogs{
			"us-west-2": {count: 1}, "af-south-1": {count: 1}, "eu-west-1": {count: 1},
		},
	).Run(context.Background())

	if got := Configured(found); strings.Join(got, ",") != "af-south-1,eu-west-1,us-west-2" {
		t.Fatalf("unordered: %v", got)
	}
}

func TestARegionThatCouldNotBeAskedIsNotReportedAsEmpty(t *testing.T) {
	// "We could not check eu-west-1" and "eu-west-1 has no flow logs" are
	// different sentences and only one of them is reassuring. Dropping the
	// first turns a permissions problem into a shorter list.
	found, err := survey(
		[]string{"us-east-1", "eu-west-1"},
		map[string]fakeFlowLogs{
			"us-east-1": {count: 1},
			"eu-west-1": {err: errors.New("UnauthorizedOperation")},
		},
	).Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if got := Unreachable(found); strings.Join(got, ",") != "eu-west-1" {
		t.Fatalf("unreachable: %v", got)
	}
	if got := Configured(found); strings.Join(got, ",") != "us-east-1" {
		t.Fatalf("an unaskable region was counted as configured: %v", got)
	}
}

func TestEachRegionIsAskedAboutItself(t *testing.T) {
	// DescribeFlowLogs is answered by the region it is sent to. Asking
	// us-east-1 about eu-west-1 returns nothing, which reads as "no flow logs
	// there" and is wrong every time.
	var asked []string
	s := &Survey{
		Regions: fakeRegions{names: []string{"us-east-1", "eu-west-1"}},
		FlowLogsIn: func(region string) awsread.FlowLogAPI {
			asked = append(asked, region)
			return fakeFlowLogs{count: 1}
		},
	}
	if _, err := s.Run(context.Background()); err != nil {
		t.Fatal(err)
	}
	if strings.Join(asked, ",") != "us-east-1,eu-west-1" {
		t.Fatalf("regions asked: %v", asked)
	}
}

func TestAFailureToListRegionsIsAnError(t *testing.T) {
	s := &Survey{
		Regions:    fakeRegions{err: errors.New("UnauthorizedOperation")},
		FlowLogsIn: func(string) awsread.FlowLogAPI { return fakeFlowLogs{} },
	}
	if _, err := s.Run(context.Background()); err == nil {
		t.Fatal("an account we cannot enumerate must not come back as one region")
	}
}

func TestASurveyWithNothingWiredUpRefusesRatherThanReturningNothing(t *testing.T) {
	if _, err := (&Survey{}).Run(context.Background()); err == nil {
		t.Fatal("an unwired survey returning an empty list reads as a single-region account")
	}
}
