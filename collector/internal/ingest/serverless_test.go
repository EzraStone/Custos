package ingest

import (
	"context"
	"errors"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ecs"
	ecstypes "github.com/aws/aws-sdk-go-v2/service/ecs/types"
	"github.com/aws/aws-sdk-go-v2/service/lambda"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

type fakeServerless struct {
	functionRoles map[string]string
	clusters      []string
	tasks         map[string][]ecstypes.Task // cluster -> tasks
	taskDefRoles  map[string]string
	taskDefCalls  int
	describeCalls int
	functionErr   error
}

func (f *fakeServerless) GetFunctionConfiguration(_ context.Context,
	in *lambda.GetFunctionConfigurationInput,
	_ ...func(*lambda.Options)) (*lambda.GetFunctionConfigurationOutput, error) {
	if f.functionErr != nil {
		return nil, f.functionErr
	}
	role, ok := f.functionRoles[aws.ToString(in.FunctionName)]
	if !ok {
		return nil, errors.New("ResourceNotFoundException")
	}
	return &lambda.GetFunctionConfigurationOutput{Role: aws.String(role)}, nil
}

func (f *fakeServerless) ListClusters(context.Context, *ecs.ListClustersInput,
	...func(*ecs.Options)) (*ecs.ListClustersOutput, error) {
	return &ecs.ListClustersOutput{ClusterArns: f.clusters}, nil
}

func (f *fakeServerless) DescribeTasks(_ context.Context, in *ecs.DescribeTasksInput,
	_ ...func(*ecs.Options)) (*ecs.DescribeTasksOutput, error) {
	f.describeCalls++
	return &ecs.DescribeTasksOutput{Tasks: f.tasks[aws.ToString(in.Cluster)]}, nil
}

func (f *fakeServerless) DescribeTaskDefinition(_ context.Context,
	in *ecs.DescribeTaskDefinitionInput,
	_ ...func(*ecs.Options)) (*ecs.DescribeTaskDefinitionOutput, error) {
	f.taskDefCalls++
	role, ok := f.taskDefRoles[aws.ToString(in.TaskDefinition)]
	if !ok {
		return nil, errors.New("ClientException")
	}
	return &ecs.DescribeTaskDefinitionOutput{
		TaskDefinition: &ecstypes.TaskDefinition{TaskRoleArn: aws.String(role)},
	}, nil
}

func task(taskDef string, interfaceIDs ...string) ecstypes.Task {
	t := ecstypes.Task{TaskDefinitionArn: aws.String(taskDef)}
	for _, id := range interfaceIDs {
		t.Attachments = append(t.Attachments, ecstypes.Attachment{
			Details: []ecstypes.KeyValuePair{
				{Name: aws.String("networkInterfaceId"), Value: aws.String(id)},
			},
		})
	}
	return t
}

func lambdaAttribution(eni, function string) Attribution {
	return Attribution{
		Attachment: wire.Attachment{InterfaceID: eni, Compute: "Lambda"},
		Degraded: `Lambda function "` + function +
			`" — execution role needs a Lambda API call not yet wired up`,
	}
}

func TestLambdaExecutionRoleIsResolved(t *testing.T) {
	api := &fakeServerless{functionRoles: map[string]string{
		"finance-close": "arn:aws:iam::447120043318:role/finance-close",
	}}
	got := NewServerless(api).Enrich(context.Background(),
		[]Attribution{lambdaAttribution("eni-1", "finance-close")})

	if got[0].Principal != "arn:aws:iam::447120043318:role/finance-close" {
		t.Fatalf("role not resolved: %+v", got[0])
	}
	if got[0].Degraded != "" {
		t.Fatalf("still degraded after resolution: %q", got[0].Degraded)
	}
}

// TestAFailedLookupLeavesTheFindingDegradedNotMissing: a Lambda whose role we
// could not read is still a reported agent without blast radius, which beats
// an agent that vanished because one call failed.
func TestAFailedLookupLeavesTheFindingDegradedNotMissing(t *testing.T) {
	api := &fakeServerless{functionErr: errors.New("AccessDeniedException")}
	got := NewServerless(api).Enrich(context.Background(),
		[]Attribution{lambdaAttribution("eni-1", "finance-close")})

	if len(got) != 1 {
		t.Fatal("the attribution must survive a failed lookup")
	}
	if got[0].Principal != "" || got[0].Degraded == "" {
		t.Fatalf("expected it to stay degraded: %+v", got[0])
	}
}

func TestAlreadyResolvedAttributionsAreLeftAlone(t *testing.T) {
	api := &fakeServerless{}
	in := []Attribution{{
		Attachment: wire.Attachment{
			InterfaceID: "eni-1", Compute: "EC2",
			Principal: "arn:aws:iam::1:role/already-known",
		},
	}}
	got := NewServerless(api).Enrich(context.Background(), in)
	if got[0].Principal != "arn:aws:iam::1:role/already-known" {
		t.Fatalf("resolved attribution was disturbed: %+v", got[0])
	}
}

func TestECSTaskRoleIsResolvedAcrossClusters(t *testing.T) {
	api := &fakeServerless{
		clusters: []string{"arn:cluster/a", "arn:cluster/b"},
		tasks: map[string][]ecstypes.Task{
			"arn:cluster/b": {task("arn:taskdef/support:3", "eni-7")},
		},
		taskDefRoles: map[string]string{
			"arn:taskdef/support:3": "arn:aws:iam::1:role/support-triage-task",
		},
	}
	got := NewServerless(api).Enrich(context.Background(), []Attribution{{
		Attachment: wire.Attachment{InterfaceID: "eni-7", Compute: "ECS"},
		Degraded:   "ECS task — task role is three API calls behind the attachment ARN",
	}})

	if got[0].Principal != "arn:aws:iam::1:role/support-triage-task" {
		t.Fatalf("task role not resolved: %+v", got[0])
	}
}

// TestTaskRoleNotExecutionRole: ExecutionRoleArn only pulls images and writes
// logs. Reporting it would name a principal that never makes a model call.
func TestTaskRoleNotExecutionRole(t *testing.T) {
	api := &fakeServerless{
		clusters: []string{"arn:cluster/a"},
		tasks: map[string][]ecstypes.Task{
			"arn:cluster/a": {task("arn:taskdef/x:1", "eni-1")},
		},
		taskDefRoles: map[string]string{"arn:taskdef/x:1": "arn:aws:iam::1:role/task-role"},
	}
	got := NewServerless(api).Enrich(context.Background(), []Attribution{{
		Attachment: wire.Attachment{InterfaceID: "eni-1", Compute: "ECS"},
		Degraded:   "ECS task",
	}})
	if got[0].Principal != "arn:aws:iam::1:role/task-role" {
		t.Fatalf("wrong role: %+v", got[0])
	}
}

// TestTaskDefinitionsAreCached: a service with twenty tasks shares one
// definition, and fetching it twenty times is twenty billable calls for one
// answer.
func TestTaskDefinitionsAreCached(t *testing.T) {
	api := &fakeServerless{
		clusters: []string{"arn:cluster/a"},
		tasks: map[string][]ecstypes.Task{
			"arn:cluster/a": {
				task("arn:taskdef/x:1", "eni-1"),
				task("arn:taskdef/x:1", "eni-2"),
				task("arn:taskdef/x:1", "eni-3"),
			},
		},
		taskDefRoles: map[string]string{"arn:taskdef/x:1": "arn:aws:iam::1:role/svc"},
	}
	s := NewServerless(api)
	got := s.Enrich(context.Background(), []Attribution{
		{Attachment: wire.Attachment{InterfaceID: "eni-1", Compute: "ECS"}},
		{Attachment: wire.Attachment{InterfaceID: "eni-2", Compute: "ECS"}},
		{Attachment: wire.Attachment{InterfaceID: "eni-3", Compute: "ECS"}},
	})

	for i, a := range got {
		if a.Principal != "arn:aws:iam::1:role/svc" {
			t.Fatalf("attribution %d unresolved: %+v", i, a)
		}
	}
	if api.taskDefCalls != 1 {
		t.Fatalf("expected the task definition to be fetched once, got %d", api.taskDefCalls)
	}
}

func TestClusterSearchIsBounded(t *testing.T) {
	var clusters []string
	for i := 0; i < MaxClustersSearched+15; i++ {
		clusters = append(clusters, "arn:cluster/"+string(rune('a'+i%26))+string(rune('0'+i/26)))
	}
	api := &fakeServerless{clusters: clusters}
	NewServerless(api).Enrich(context.Background(), []Attribution{
		{Attachment: wire.Attachment{InterfaceID: "eni-missing", Compute: "ECS"}},
	})
	if api.describeCalls > MaxClustersSearched {
		t.Fatalf("searched %d clusters, bound is %d", api.describeCalls, MaxClustersSearched)
	}
}

func TestNilResolverIsANoOp(t *testing.T) {
	in := []Attribution{{Attachment: wire.Attachment{InterfaceID: "eni-1"}}}
	var s *Serverless
	if got := s.Enrich(context.Background(), in); len(got) != 1 {
		t.Fatal("a nil resolver must pass attributions through unchanged")
	}
}

func TestFunctionNameExtraction(t *testing.T) {
	name := FunctionNameFrom(
		"AWS Lambda VPC ENI-finance-close-3f2504e0-4f89-11d3-9a0c-0305e82c3301")
	if name != "finance-close" {
		t.Fatalf("got %q", name)
	}
	if FunctionNameFrom("primary") != "" {
		t.Fatal("a non-Lambda description must yield nothing")
	}
}
