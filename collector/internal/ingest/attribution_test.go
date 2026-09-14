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
	// instanceNames is what somebody called the instance, which is what
	// destination naming reads when the interface itself carries nothing.
	instanceNames map[string]string
	// endpoints maps a vpce- id to the AWS endpoint service it is for, which
	// is how the collector learns that a private address in the customer's own
	// subnet is Bedrock.
	endpoints map[string]string
	// endpointTags is the Name the customer put on the endpoint itself, which
	// comes back on the same call as the service name.
	endpointTags map[string]string
	// privateDNS maps an endpoint service name to the DNS name its publisher
	// configured, which is what DescribeVpcEndpointServices answers.
	privateDNS         map[string]string
	describes          int
	describedByAddress int
	describedInstances int
	describedEndpoints int
	describedServices  int
}

func (f *fakeEC2) DescribeVpcEndpoints(_ context.Context, in *ec2.DescribeVpcEndpointsInput,
	_ ...func(*ec2.Options)) (*ec2.DescribeVpcEndpointsOutput, error) {
	f.describedEndpoints++
	out := &ec2.DescribeVpcEndpointsOutput{}
	for _, id := range in.VpcEndpointIds {
		service, ok := f.endpoints[id]
		if !ok {
			continue
		}
		endpoint := ec2types.VpcEndpoint{
			VpcEndpointId: aws.String(id), ServiceName: aws.String(service),
		}
		if name, ok := f.endpointTags[id]; ok {
			endpoint.Tags = []ec2types.Tag{
				{Key: aws.String("Name"), Value: aws.String(name)},
			}
		}
		out.VpcEndpoints = append(out.VpcEndpoints, endpoint)
	}
	return out, nil
}

func (f *fakeEC2) DescribeVpcEndpointServices(_ context.Context,
	in *ec2.DescribeVpcEndpointServicesInput,
	_ ...func(*ec2.Options)) (*ec2.DescribeVpcEndpointServicesOutput, error) {
	f.describedServices++
	out := &ec2.DescribeVpcEndpointServicesOutput{}
	for _, name := range in.ServiceNames {
		dns, ok := f.privateDNS[name]
		if !ok {
			continue
		}
		out.ServiceDetails = append(out.ServiceDetails, ec2types.ServiceDetail{
			ServiceName: aws.String(name), PrivateDnsName: aws.String(dns),
		})
	}
	return out, nil
}

func (f *fakeEC2) DescribeNetworkInterfaces(_ context.Context, in *ec2.DescribeNetworkInterfacesInput,
	_ ...func(*ec2.Options)) (*ec2.DescribeNetworkInterfacesOutput, error) {
	f.describes++
	out := &ec2.DescribeNetworkInterfacesOutput{}

	// Two ways to ask: by interface id, which is how attribution looks up the
	// ENIs a flow log named, and by private address, which is how destination
	// naming asks what lives at an address.
	if len(in.Filters) > 0 {
		f.describedByAddress++
		want := map[string]bool{}
		for _, filter := range in.Filters {
			if aws.ToString(filter.Name) != "addresses.private-ip-address" {
				continue
			}
			for _, v := range filter.Values {
				want[v] = true
			}
		}
		for _, iface := range f.interfaces {
			for _, a := range iface.PrivateIpAddresses {
				if want[aws.ToString(a.PrivateIpAddress)] {
					out.NetworkInterfaces = append(out.NetworkInterfaces, iface)
					break
				}
			}
		}
		return out, nil
	}

	want := map[string]bool{}
	for _, id := range in.NetworkInterfaceIds {
		want[id] = true
	}
	for _, iface := range f.interfaces {
		if want[aws.ToString(iface.NetworkInterfaceId)] {
			out.NetworkInterfaces = append(out.NetworkInterfaces, iface)
		}
	}
	return out, nil
}

func (f *fakeEC2) DescribeInstances(_ context.Context, in *ec2.DescribeInstancesInput,
	_ ...func(*ec2.Options)) (*ec2.DescribeInstancesOutput, error) {
	f.describedInstances++
	out := &ec2.DescribeInstancesOutput{}
	var instances []ec2types.Instance
	for _, id := range in.InstanceIds {
		instance := ec2types.Instance{InstanceId: aws.String(id)}
		if arn, ok := f.instances[id]; ok {
			instance.IamInstanceProfile = &ec2types.IamInstanceProfile{Arn: aws.String(arn)}
		}
		if name, ok := f.instanceNames[id]; ok {
			instance.Tags = []ec2types.Tag{{Key: aws.String("Name"), Value: aws.String(name)}}
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
