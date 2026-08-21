package awsread

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	"github.com/aws/aws-sdk-go-v2/service/ecs"
	"github.com/aws/aws-sdk-go-v2/service/iam"
	"github.com/aws/aws-sdk-go-v2/service/lambda"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

// The interfaces below are the collector's entire AWS surface.
//
// They are the strongest form of SEC-16 available in Go: a write is not
// refused at runtime, it is un-callable, because no method that performs one is
// declared. The verb allowlist in awsread.go remains as a second layer and as
// the audit trail a customer can read, but this is the mechanism.
//
// Adding a method here is the moment to ask whether it writes. There are few
// enough of them that the question is easy to answer by reading the file.

// LogsAPI reads CloudWatch Logs.
//
// Note the absence of StartQuery. Logs Insights would be a convenient way to
// filter flow logs and it creates a billable query in the customer's account,
// which is a write however read-only the name sounds.
type LogsAPI interface {
	FilterLogEvents(context.Context, *cloudwatchlogs.FilterLogEventsInput,
		...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.FilterLogEventsOutput, error)
	DescribeLogGroups(context.Context, *cloudwatchlogs.DescribeLogGroupsInput,
		...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.DescribeLogGroupsOutput, error)
}

// ObjectAPI reads flow logs delivered to S3.
type ObjectAPI interface {
	ListObjectsV2(context.Context, *s3.ListObjectsV2Input,
		...func(*s3.Options)) (*s3.ListObjectsV2Output, error)
	GetObject(context.Context, *s3.GetObjectInput,
		...func(*s3.Options)) (*s3.GetObjectOutput, error)
}

// NetworkAPI resolves interfaces to the compute behind them.
type NetworkAPI interface {
	DescribeNetworkInterfaces(context.Context, *ec2.DescribeNetworkInterfacesInput,
		...func(*ec2.Options)) (*ec2.DescribeNetworkInterfacesOutput, error)
	DescribeInstances(context.Context, *ec2.DescribeInstancesInput,
		...func(*ec2.Options)) (*ec2.DescribeInstancesOutput, error)
}

// IdentityAPI reads roles, their tags, and what their policies permit.
//
// The policy read is what lets a finding say "can write to billing" rather
// than only "talked to billing", which is the difference between a curiosity
// and a budget line.
type IdentityAPI interface {
	GetRole(context.Context, *iam.GetRoleInput,
		...func(*iam.Options)) (*iam.GetRoleOutput, error)
	ListRoleTags(context.Context, *iam.ListRoleTagsInput,
		...func(*iam.Options)) (*iam.ListRoleTagsOutput, error)
	ListAttachedRolePolicies(context.Context, *iam.ListAttachedRolePoliciesInput,
		...func(*iam.Options)) (*iam.ListAttachedRolePoliciesOutput, error)
	ListRolePolicies(context.Context, *iam.ListRolePoliciesInput,
		...func(*iam.Options)) (*iam.ListRolePoliciesOutput, error)
	GetRolePolicy(context.Context, *iam.GetRolePolicyInput,
		...func(*iam.Options)) (*iam.GetRolePolicyOutput, error)
	GetPolicy(context.Context, *iam.GetPolicyInput,
		...func(*iam.Options)) (*iam.GetPolicyOutput, error)
	GetPolicyVersion(context.Context, *iam.GetPolicyVersionInput,
		...func(*iam.Options)) (*iam.GetPolicyVersionOutput, error)
}

// ServerlessAPI resolves the execution role behind a Lambda or ECS interface.
//
// These exist because attribution without them stops at "something on this
// interface is an agent", which is a finding nobody can action. On an account
// that runs its agents on Lambda — and many do — that is the difference between
// a useful report and a curiosity.
type ServerlessAPI interface {
	GetFunctionConfiguration(context.Context, *lambda.GetFunctionConfigurationInput,
		...func(*lambda.Options)) (*lambda.GetFunctionConfigurationOutput, error)
	DescribeTasks(context.Context, *ecs.DescribeTasksInput,
		...func(*ecs.Options)) (*ecs.DescribeTasksOutput, error)
	DescribeTaskDefinition(context.Context, *ecs.DescribeTaskDefinitionInput,
		...func(*ecs.Options)) (*ecs.DescribeTaskDefinitionOutput, error)
	ListClusters(context.Context, *ecs.ListClustersInput,
		...func(*ecs.Options)) (*ecs.ListClustersOutput, error)
}

// Compile-time assertions that the real SDK clients satisfy these interfaces.
// If AWS changes a signature, this fails at build time rather than at 3am in a
// customer's account.
var (
	_ LogsAPI     = (*cloudwatchlogs.Client)(nil)
	_ ObjectAPI   = (*s3.Client)(nil)
	_ NetworkAPI  = (*ec2.Client)(nil)
	_ IdentityAPI = (*iam.Client)(nil)
)

// ServerlessAPI is satisfied by a pair of clients rather than one, so it has
// no single compile-time assertion. `Serverless` in package ingest composes
// them; these assertions cover each half.
var (
	_ interface {
		GetFunctionConfiguration(context.Context, *lambda.GetFunctionConfigurationInput,
			...func(*lambda.Options)) (*lambda.GetFunctionConfigurationOutput, error)
	} = (*lambda.Client)(nil)

	_ interface {
		DescribeTasks(context.Context, *ecs.DescribeTasksInput,
			...func(*ecs.Options)) (*ecs.DescribeTasksOutput, error)
	} = (*ecs.Client)(nil)
)
