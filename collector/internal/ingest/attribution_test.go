package ingest

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"
)

type fakeEC2 struct {
	interfaces []ec2types.NetworkInterface
	instances  map[string]string // instance id -> instance profile ARN
	describes  int
}

func (f *fakeEC2) DescribeNetworkInterfaces(_ context.Context, in *ec2.DescribeNetworkInterfacesInput,
	_ ...func(*ec2.Options)) (*ec2.DescribeNetworkInterfacesOutput, error) {
	f.describes++
	want := map[string]bool{}
	for _, id := range in.NetworkInterfaceIds {
		want[id] = true
	}
	out := &ec2.DescribeNetworkInterfacesOutput{}
	for _, iface := range f.interfaces {
		if want[aws.ToString(iface.NetworkInterfaceId)] {
			out.NetworkInterfaces = append(out.NetworkInterfaces, iface)
		}
	}
	return out, nil
}

func (f *fakeEC2) DescribeInstances(_ context.Context, in *ec2.DescribeInstancesInput,
	_ ...func(*ec2.Options)) (*ec2.DescribeInstancesOutput, error) {
	out := &ec2.DescribeInstancesOutput{}
	var instances []ec2types.Instance
	for _, id := range in.InstanceIds {
		instance := ec2types.Instance{InstanceId: aws.String(id)}
		if arn, ok := f.instances[id]; ok {
			instance.IamInstanceProfile = &ec2types.IamInstanceProfile{Arn: aws.String(arn)}
		}
		instances = append(instances, instance)
	}
	out.Reservations = []ec2types.Reservation{{Instances: instances}}
	return out, nil
}

func eni(id, description string, opts ...func(*ec2types.NetworkInterface)) ec2types.NetworkInterface {
	iface := ec2types.NetworkInterface{
		NetworkInterfaceId: aws.String(id),
		Description:        aws.String(description),
		PrivateIpAddress:   aws.String("10.0.20.11"),
		SubnetId:           aws.String("subnet-1"),
		InterfaceType:      ec2types.NetworkInterfaceTypeInterface,
	}
	for _, o := range opts {
		o(&iface)
	}
	return iface
}

func onInstance(id string) func(*ec2types.NetworkInterface) {
	return func(i *ec2types.NetworkInterface) {
		i.Attachment = &ec2types.NetworkInterfaceAttachment{InstanceId: aws.String(id)}
	}
}

func resolve(t *testing.T, api *fakeEC2, ids ...string) []Attribution {
	t.Helper()
	r := &Resolver{API: api, AccountID: "447120043318"}
	out, err := r.Resolve(context.Background(), ids)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestEC2ResolvesToTheInstanceProfileRole(t *testing.T) {
	api := &fakeEC2{
		interfaces: []ec2types.NetworkInterface{eni("eni-1", "primary", onInstance("i-abc"))},
		instances: map[string]string{
			"i-abc": "arn:aws:iam::447120043318:instance-profile/autofix-runner",
		},
	}
	got := resolve(t, api, "eni-1")
	if len(got) != 1 {
		t.Fatalf("got %d attributions", len(got))
	}
	if got[0].Principal != "arn:aws:iam::447120043318:role/autofix-runner" {
		t.Fatalf("bad principal %q", got[0].Principal)
	}
	if got[0].Compute != "EC2" || got[0].Degraded != "" {
		t.Fatalf("unexpected %+v", got[0])
	}
}

func TestInstanceWithoutAProfileIsDegradedNotGuessed(t *testing.T) {
	api := &fakeEC2{
		interfaces: []ec2types.NetworkInterface{eni("eni-1", "primary", onInstance("i-abc"))},
		instances:  map[string]string{},
	}
	got := resolve(t, api, "eni-1")
	if got[0].Principal != "" {
		t.Fatalf("must not invent a principal, got %q", got[0].Principal)
	}
	if !strings.Contains(got[0].Degraded, "instance profile") {
		t.Fatalf("degraded reason unhelpful: %q", got[0].Degraded)
	}
}

func TestLambdaInterfaceYieldsComputeAndFunctionName(t *testing.T) {
	api := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		eni("eni-2", "AWS Lambda VPC ENI-finance-close-3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
	}}
	got := resolve(t, api, "eni-2")
	if got[0].Compute != "Lambda" {
		t.Fatalf("compute not detected: %+v", got[0])
	}
	if !strings.Contains(got[0].Degraded, "finance-close") {
		t.Fatalf("function name not extracted: %q", got[0].Degraded)
	}
}

func TestECSAndEKSInterfacesAreRecognised(t *testing.T) {
	api := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		eni("eni-3", "arn:aws:ecs:us-east-1:447120043318:attachment/abc-def"),
		eni("eni-4", "aws-K8S-i-0123456789abcdef0"),
	}}
	got := resolve(t, api, "eni-3", "eni-4")
	byID := map[string]Attribution{}
	for _, a := range got {
		byID[a.InterfaceID] = a
	}
	if byID["eni-3"].Compute != "ECS" {
		t.Errorf("ECS not detected: %+v", byID["eni-3"])
	}
	if byID["eni-4"].Compute != "EKS" {
		t.Errorf("EKS not detected: %+v", byID["eni-4"])
	}
	if !strings.Contains(byID["eni-4"].Degraded, "pod-level") {
		t.Errorf("EKS ceiling not explained: %q", byID["eni-4"].Degraded)
	}
}

// TestCustomerTagBeatsEveryInference: their metadata about their own
// infrastructure is better than anything we can derive.
func TestCustomerTagBeatsEveryInference(t *testing.T) {
	api := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		eni("eni-5", "aws-K8S-i-0123456789abcdef0", func(i *ec2types.NetworkInterface) {
			i.TagSet = []ec2types.Tag{{
				Key:   aws.String("custos:principal"),
				Value: aws.String("arn:aws:iam::447120043318:role/ops-automation"),
			}}
		}),
	}}
	got := resolve(t, api, "eni-5")
	if got[0].Principal != "arn:aws:iam::447120043318:role/ops-automation" {
		t.Fatalf("tag ignored: %+v", got[0])
	}
	if got[0].Degraded != "" {
		t.Fatalf("tagged interface should not be degraded: %q", got[0].Degraded)
	}
}

func TestEmptyTagValueDoesNotOverrideInference(t *testing.T) {
	api := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		eni("eni-6", "primary", onInstance("i-abc"), func(i *ec2types.NetworkInterface) {
			i.TagSet = []ec2types.Tag{{Key: aws.String("custos:principal"), Value: aws.String("")}}
		}),
	}, instances: map[string]string{
		"i-abc": "arn:aws:iam::447120043318:instance-profile/autofix-runner",
	}}
	got := resolve(t, api, "eni-6")
	if got[0].Principal == "" {
		t.Fatal("an empty tag must not clear a resolved principal")
	}
}

func TestNonWorkloadInterfacesAreLabelled(t *testing.T) {
	api := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		eni("eni-7", "NAT gateway", func(i *ec2types.NetworkInterface) {
			i.InterfaceType = ec2types.NetworkInterfaceTypeNatGateway
		}),
	}}
	got := resolve(t, api, "eni-7")
	if !strings.Contains(got[0].Degraded, "not a workload") {
		t.Fatalf("gateway interface not labelled: %+v", got[0])
	}
}

// TestSEC20ResolvedAndDegradedAreNeverMerged.
func TestSEC20ResolvedAndDegradedAreNeverMerged(t *testing.T) {
	api := &fakeEC2{
		interfaces: []ec2types.NetworkInterface{
			eni("eni-1", "primary", onInstance("i-abc")),
			eni("eni-2", "AWS Lambda VPC ENI-x-3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
		},
		instances: map[string]string{
			"i-abc": "arn:aws:iam::447120043318:instance-profile/autofix-runner",
		},
	}
	resolved, degraded := Attachments(resolve(t, api, "eni-1", "eni-2"))
	if len(resolved) != 1 || len(degraded) != 1 {
		t.Fatalf("resolved=%d degraded=%d", len(resolved), len(degraded))
	}
	if resolved[0].InterfaceID != "eni-1" {
		t.Fatalf("wrong interface resolved: %+v", resolved[0])
	}
}

// TestInterfacesAreBatched: one request per ENI would be thousands of billable
// calls on a real account.
func TestInterfacesAreBatched(t *testing.T) {
	var ids []string
	var ifaces []ec2types.NetworkInterface
	for i := 0; i < 450; i++ {
		id := fmt.Sprintf("eni-%06d", i)
		ids = append(ids, id)
		ifaces = append(ifaces, eni(id, "primary"))
	}
	api := &fakeEC2{interfaces: ifaces}
	got := resolve(t, api, ids...)

	if len(got) != 450 {
		t.Fatalf("got %d attributions", len(got))
	}
	if api.describes != 3 {
		t.Fatalf("expected 3 batched calls for 450 interfaces, got %d", api.describes)
	}
}

func TestEmptyInputIsNotAnAPICall(t *testing.T) {
	api := &fakeEC2{}
	if got := resolve(t, api); got != nil {
		t.Fatalf("expected nil, got %v", got)
	}
	if api.describes != 0 {
		t.Fatal("no interfaces should mean no API call")
	}
}
