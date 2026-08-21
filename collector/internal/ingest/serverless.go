package ingest

import (
	"context"
	"regexp"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ecs"
	ecstypes "github.com/aws/aws-sdk-go-v2/service/ecs/types"
	"github.com/aws/aws-sdk-go-v2/service/lambda"

	"github.com/EzraStone/Custos/collector/internal/awsread"
)

// Resolving the role behind a Lambda or ECS interface takes more calls than
// EC2 does, and the paths are different enough to be worth stating:
//
//	Lambda  ENI description embeds the function name. GetFunctionConfiguration
//	        turns that into the execution role. One call.
//	ECS     ENI description is an attachment ARN, which names neither the task
//	        nor the cluster. DescribeTasks needs both, so the cluster has to be
//	        found first — and an account can have many.
//
// The ECS path is therefore best-effort across clusters, and its cost is
// bounded: a fixed number of clusters searched, and a cache so the same task
// definition is never fetched twice in one run. An unbounded search across a
// large account would spend the customer's API budget resolving one interface.

// MaxClustersSearched bounds the ECS lookup.
//
// Beyond this the interface is reported as unattributed rather than found
// slowly. A finding that costs a customer a rate-limit incident is not worth
// having, and the tag fallback in attribution.go covers the accounts where this
// matters most.
const MaxClustersSearched = 20

var lambdaFunctionName = regexp.MustCompile(`^AWS Lambda VPC ENI[- ](?P<fn>.+?)-[0-9a-f-]{36}$`)

// Serverless resolves execution roles for Lambda and ECS interfaces.
type Serverless struct {
	API awsread.ServerlessAPI

	// taskDefRoles caches task definition ARN -> role, because a service with
	// twenty tasks shares one definition and fetching it twenty times is
	// twenty billable calls for one answer.
	taskDefRoles map[string]string
	clusters     []string
}

func NewServerless(api awsread.ServerlessAPI) *Serverless {
	return &Serverless{API: api, taskDefRoles: map[string]string{}}
}

// Enrich fills in principals for Lambda and ECS attributions that EC2
// resolution could not reach.
//
// Failures leave an attribution degraded rather than propagating. A Lambda
// whose role we could not read is still a reported agent without blast radius,
// which beats an agent that vanished from the report because one call failed.
func (s *Serverless) Enrich(ctx context.Context, attributions []Attribution) []Attribution {
	if s == nil || s.API == nil {
		return attributions
	}

	out := make([]Attribution, len(attributions))
	copy(out, attributions)

	for i := range out {
		if out[i].Principal != "" {
			continue
		}
		switch out[i].Compute {
		case "Lambda":
			if role := s.lambdaRole(ctx, out[i].Degraded); role != "" {
				out[i].Principal = role
				out[i].Degraded = ""
			}
		case "ECS":
			if role := s.ecsRole(ctx, out[i].InterfaceID); role != "" {
				out[i].Principal = role
				out[i].Degraded = ""
			}
		}
	}
	return out
}

// FunctionNameFrom extracts a Lambda function name from an ENI description.
func FunctionNameFrom(description string) string {
	if m := lambdaFunctionName.FindStringSubmatch(description); m != nil {
		return m[1]
	}
	return ""
}

// lambdaRole resolves an execution role from a degraded reason carrying the
// function name.
//
// The function name is quoted inside the degraded message that attribution.go
// produced. Threading it through a string is not elegant; the alternative is a
// second field on Attribution that exists only for this handoff, and a struct
// field that means something to exactly one caller is its own kind of mess.
func (s *Serverless) lambdaRole(ctx context.Context, degraded string) string {
	name := quotedName(degraded)
	if name == "" {
		return ""
	}
	out, err := s.API.GetFunctionConfiguration(ctx, &lambda.GetFunctionConfigurationInput{
		FunctionName: aws.String(name),
	})
	if err != nil || out == nil {
		return ""
	}
	return aws.ToString(out.Role)
}

func quotedName(s string) string {
	start := strings.Index(s, `"`)
	if start < 0 {
		return ""
	}
	rest := s[start+1:]
	end := strings.Index(rest, `"`)
	if end < 0 {
		return ""
	}
	return rest[:end]
}

// ecsRole finds the task using this interface and returns its task role.
func (s *Serverless) ecsRole(ctx context.Context, interfaceID string) string {
	for _, cluster := range s.clusterList(ctx) {
		tasks, err := s.API.DescribeTasks(ctx, &ecs.DescribeTasksInput{
			Cluster: aws.String(cluster),
		})
		if err != nil || tasks == nil {
			continue
		}
		for _, task := range tasks.Tasks {
			if !taskUsesInterface(task.Attachments, interfaceID) {
				continue
			}
			return s.taskDefinitionRole(ctx, aws.ToString(task.TaskDefinitionArn))
		}
	}
	return ""
}

func taskUsesInterface(attachments []ecstypes.Attachment, interfaceID string) bool {
	for _, attachment := range attachments {
		for _, detail := range attachment.Details {
			if aws.ToString(detail.Name) == "networkInterfaceId" &&
				aws.ToString(detail.Value) == interfaceID {
				return true
			}
		}
	}
	return false
}

func (s *Serverless) taskDefinitionRole(ctx context.Context, taskDefARN string) string {
	if taskDefARN == "" {
		return ""
	}
	if role, ok := s.taskDefRoles[taskDefARN]; ok {
		return role
	}

	out, err := s.API.DescribeTaskDefinition(ctx, &ecs.DescribeTaskDefinitionInput{
		TaskDefinition: aws.String(taskDefARN),
	})
	role := ""
	if err == nil && out != nil && out.TaskDefinition != nil {
		// TaskRoleArn is what the container assumes; ExecutionRoleArn only
		// pulls images and writes logs. Reporting the execution role would
		// name a principal that never makes a model call.
		role = aws.ToString(out.TaskDefinition.TaskRoleArn)
	}
	s.taskDefRoles[taskDefARN] = role
	return role
}

func (s *Serverless) clusterList(ctx context.Context) []string {
	if s.clusters != nil {
		return s.clusters
	}
	s.clusters = []string{}

	out, err := s.API.ListClusters(ctx, &ecs.ListClustersInput{})
	if err != nil || out == nil {
		return s.clusters
	}
	for i, arn := range out.ClusterArns {
		if i >= MaxClustersSearched {
			break
		}
		s.clusters = append(s.clusters, arn)
	}
	return s.clusters
}
