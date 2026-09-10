// Package awsclient builds real AWS clients from the ambient credential chain.
//
// Kept separate from the collection logic so that everything in `ingest` is
// testable against fakes, and so that this file — the only place a real AWS
// connection is created — is short enough to read in one sitting.
package awsclient

import (
	"context"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/aws/retry"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials/stscreds"
	"github.com/aws/aws-sdk-go-v2/service/cloudtrail"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	"github.com/aws/aws-sdk-go-v2/service/ecs"
	"github.com/aws/aws-sdk-go-v2/service/iam"
	"github.com/aws/aws-sdk-go-v2/service/lambda"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/sts"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

// Options describes how to reach the account being scanned.
type Options struct {
	Region string

	// RoleARN is the cross-account role to assume. Empty means use the ambient
	// credentials directly, which is the mode for a customer running the
	// collector inside their own account.
	RoleARN string

	// ExternalID guards against the confused deputy problem. Required whenever
	// RoleARN is set — a cross-account role without one can be assumed by
	// anyone who learns the ARN.
	ExternalID string
}

// Clients is the set of AWS clients the collector uses.
type Clients struct {
	Logs     *cloudwatchlogs.Client
	Objects  *s3.Client
	Network  *ec2.Client
	Identity *iam.Client
	Trail    *cloudtrail.Client

	// FlowLogsIn returns an EC2 client bound to one region.
	//
	// DescribeFlowLogs is answered by the region it is sent to, so a survey of
	// what an account runs where cannot be done from a single client. This is
	// the only place in the collector that talks to a region other than the
	// configured one, and it does one read.
	FlowLogsIn func(region string) awsread.FlowLogAPI

	// Serverless composes the Lambda and ECS clients behind one interface,
	// because resolving a role needs both and no single SDK client provides
	// them.
	Serverless *ServerlessClients
}

// ServerlessClients satisfies awsread.ServerlessAPI by delegating each method
// to whichever SDK client owns it.
//
// Written out rather than embedded: both SDK types are named Client, so
// embedding them collides. The explicit form is longer and says exactly which
// service each call reaches, which is worth more here than brevity — this is
// the file a reviewer reads to see what the collector talks to.
type ServerlessClients struct {
	Lambda *lambda.Client
	ECS    *ecs.Client
}

func (c *ServerlessClients) GetFunctionConfiguration(
	ctx context.Context, in *lambda.GetFunctionConfigurationInput,
	opts ...func(*lambda.Options),
) (*lambda.GetFunctionConfigurationOutput, error) {
	return c.Lambda.GetFunctionConfiguration(ctx, in, opts...)
}

func (c *ServerlessClients) DescribeTasks(
	ctx context.Context, in *ecs.DescribeTasksInput, opts ...func(*ecs.Options),
) (*ecs.DescribeTasksOutput, error) {
	return c.ECS.DescribeTasks(ctx, in, opts...)
}

func (c *ServerlessClients) DescribeTaskDefinition(
	ctx context.Context, in *ecs.DescribeTaskDefinitionInput, opts ...func(*ecs.Options),
) (*ecs.DescribeTaskDefinitionOutput, error) {
	return c.ECS.DescribeTaskDefinition(ctx, in, opts...)
}

func (c *ServerlessClients) ListClusters(
	ctx context.Context, in *ecs.ListClustersInput, opts ...func(*ecs.Options),
) (*ecs.ListClustersOutput, error) {
	return c.ECS.ListClusters(ctx, in, opts...)
}

// MaxRetryAttempts is what a first scan of a large account needs.
//
// The SDK default is three, which is right for an application making a few
// calls. This makes thousands: one DescribeNetworkInterfaces page per thousand
// interfaces, an IAM read per principal, a CloudTrail lookup per unresolved
// address. On an account with a few thousand interfaces EC2 throttles, three
// attempts are spent inside a second, and the call fails.
//
// What a customer sees when it does is not an error. It is a report where half
// the findings are unattributed — the same shape an account with no resource
// tags produces — so the failure arrives disguised as a fact about them.
const MaxRetryAttempts = 8

// retryer backs off on throttling and adapts to it.
//
// Adaptive mode rate-limits the client itself once AWS starts pushing back,
// rather than retrying into a wall. That matters here because the collector's
// calls are a burst: it resolves every interface in a window at once, and the
// throttle it earns applies to the customer's whole account, not just to us.
// Being a good citizen in someone else's account is not optional for something
// they installed on our word.
func retryer() aws.Retryer {
	return retry.NewAdaptiveMode(func(o *retry.AdaptiveModeOptions) {
		o.StandardOptions = append(o.StandardOptions, func(s *retry.StandardOptions) {
			s.MaxAttempts = MaxRetryAttempts
		})
	})
}

// New builds clients, assuming a cross-account role when one is configured.
func New(ctx context.Context, opts Options) (*Clients, error) {
	cfg, err := config.LoadDefaultConfig(
		ctx,
		config.WithRegion(opts.Region),
		config.WithRetryer(retryer),
	)
	if err != nil {
		return nil, fmt.Errorf("loading AWS configuration: %w", err)
	}

	if opts.RoleARN != "" {
		if opts.ExternalID == "" {
			return nil, fmt.Errorf(
				"an external ID is required when assuming %s: a cross-account role "+
					"without one can be assumed by anyone who learns the ARN", opts.RoleARN)
		}
		provider := stscreds.NewAssumeRoleProvider(
			sts.NewFromConfig(cfg), opts.RoleARN,
			func(o *stscreds.AssumeRoleOptions) {
				o.ExternalID = aws.String(opts.ExternalID)
				o.RoleSessionName = "custos-collector"
			},
		)
		cfg.Credentials = aws.NewCredentialsCache(provider)
	}

	return &Clients{
		FlowLogsIn: func(region string) awsread.FlowLogAPI {
			return ec2.NewFromConfig(cfg, func(o *ec2.Options) { o.Region = region })
		},
		Logs:     cloudwatchlogs.NewFromConfig(cfg),
		Objects:  s3.NewFromConfig(cfg),
		Network:  ec2.NewFromConfig(cfg),
		Identity: iam.NewFromConfig(cfg),
		Trail:    cloudtrail.NewFromConfig(cfg),
		Serverless: &ServerlessClients{
			Lambda: lambda.NewFromConfig(cfg),
			ECS:    ecs.NewFromConfig(cfg),
		},
	}, nil
}

// Compile-time assertion: the composition satisfies the read-only interface.
var _ awsread.ServerlessAPI = (*ServerlessClients)(nil)
