package ingest

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"

	"github.com/EzraStone/Custos/collector/internal/wire"
)

func destEni(address, description string, tags ...string) ec2types.NetworkInterface {
	iface := ec2types.NetworkInterface{
		NetworkInterfaceId: aws.String("eni-" + address),
		Description:        aws.String(description),
		PrivateIpAddresses: []ec2types.NetworkInterfacePrivateIpAddress{
			{PrivateIpAddress: aws.String(address)},
		},
	}
	for i := 0; i+1 < len(tags); i += 2 {
		iface.TagSet = append(iface.TagSet, ec2types.Tag{
			Key: aws.String(tags[i]), Value: aws.String(tags[i+1]),
		})
	}
	return iface
}

func resolveNames(t *testing.T, ifaces []ec2types.NetworkInterface, addresses ...string) map[string]string {
	t.Helper()
	return resolveNamesWith(t, &fakeEC2{interfaces: ifaces}, addresses...)
}

func resolveNamesWith(t *testing.T, api *fakeEC2, addresses ...string) map[string]string {
	t.Helper()
	r := &DestinationResolver{API: api}
	got, err := r.Resolve(context.Background(), addresses)
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]string{}
	for _, d := range got {
		names[d.Address] = d.Name + "/" + d.Kind
	}
	return names
}

func TestALoadBalancerIsNamedByItsOwnName(t *testing.T) {
	names := resolveNames(t,
		[]ec2types.NetworkInterface{destEni("10.0.4.23", "ELB app/billing-api/50dc6c495c0c9188")},
		"10.0.4.23")
	if names["10.0.4.23"] != "billing-api/load-balancer" {
		t.Fatalf("got %v", names)
	}
}

func TestTheNameTagWinsOverTheDescription(t *testing.T) {
	// The tag is what the customer calls the thing. The description is what
	// AWS called it.
	names := resolveNames(t,
		[]ec2types.NetworkInterface{
			destEni("10.0.4.23", "ELB app/k8s-internal-abc123/50dc", "Name", "orders-api"),
		},
		"10.0.4.23")
	if names["10.0.4.23"] != "orders-api/tag" {
		t.Fatalf("got %v", names)
	}
}

func TestKnownAWSShapesAreParsed(t *testing.T) {
	cases := map[string]string{
		"VPC Endpoint Interface vpce-0a1b2c3d": "vpce-0a1b2c3d/vpc-endpoint",
		"RDSNetworkInterface":                  "rds/rds",
		"ElastiCache my-cluster-001":           "elasticache/elasticache",
		"AWS Lambda VPC ENI-report-builder-1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809": "report-builder/lambda",
		"ELB net/internal-nlb/abc123":                                            "internal-nlb/load-balancer",
	}
	for description, want := range cases {
		names := resolveNames(t, []ec2types.NetworkInterface{destEni("10.0.4.23", description)}, "10.0.4.23")
		if names["10.0.4.23"] != want {
			t.Errorf("%q: got %q, want %q", description, names["10.0.4.23"], want)
		}
	}
}

// TestFreeTextIsNotForwarded: an ENI description is a text field a person
// typed into. Shipping an unrecognised one out of a customer account would
// carry whatever they wrote — a ticket number, a colleague's name, a note
// about an incident. The address is honest and says nothing extra.
func TestFreeTextIsNotForwarded(t *testing.T) {
	names := resolveNames(t,
		[]ec2types.NetworkInterface{destEni("10.0.4.23", "tmp box for INC-4471, ask sam before deleting")},
		"10.0.4.23")
	if len(names) != 0 {
		t.Fatalf("forwarded an unrecognised description: %v", names)
	}
}

// TestPublicAddressesAreNotAskedAbout: they cannot be an ENI in this account,
// and putting them in the filter would tell AWS which third parties the
// customer talks to in a request that does not need to know.
func TestPublicAddressesAreNotAskedAbout(t *testing.T) {
	fake := &fakeEC2{interfaces: []ec2types.NetworkInterface{destEni("10.0.4.23", "RDSNetworkInterface")}}
	r := &DestinationResolver{API: fake}
	if _, err := r.Resolve(context.Background(), []string{"52.216.10.7", "160.79.104.10"}); err != nil {
		t.Fatal(err)
	}
	if fake.describes != 0 {
		t.Fatalf("called AWS for public addresses only: %d describes", fake.describes)
	}
}

func TestNothingToNameMakesNoCall(t *testing.T) {
	fake := &fakeEC2{}
	r := &DestinationResolver{API: fake}
	got, err := r.Resolve(context.Background(), nil)
	if err != nil || got != nil || fake.describes != 0 {
		t.Fatalf("got %v, err %v, describes %d", got, err, fake.describes)
	}
}

func TestAnUnnamedAddressIsOmittedNotBlank(t *testing.T) {
	// The control plane already knows how to show a bare address. An entry
	// asserting "this is called nothing" would be worse than no entry.
	names := resolveNames(t,
		[]ec2types.NetworkInterface{
			destEni("10.0.4.23", "RDSNetworkInterface"),
			destEni("10.0.4.24", ""),
		},
		"10.0.4.23", "10.0.4.24")
	if len(names) != 1 || names["10.0.4.23"] == "" {
		t.Fatalf("got %v", names)
	}
}

// TestBothEndsOfAConversationAreAskedAbout: the peer is the destination on the
// way out and the source on the way back. A workload that only ever received
// from an address still reached it, and naming only outbound destinations
// would leave those unnamed for no reason.
func TestBothEndsOfAConversationAreAskedAbout(t *testing.T) {
	records := []wire.FlowRecord{
		{DstAddr: "10.0.4.23", Direction: wire.Egress, SrcAddr: "10.0.1.5"},
		{SrcAddr: "10.0.9.44", Direction: wire.Ingress, DstAddr: "10.0.1.5"},
	}
	got := peerAddresses(records)
	want := []string{"10.0.4.23", "10.0.9.44"}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] {
		t.Fatalf("got %v, want %v", got, want)
	}

	// 10.0.1.5 is our own side on both records — the source going out and the
	// destination coming back. Asking AWS to name the workload we are already
	// attributing would be a wasted filter slot on a paginated call.
	for _, a := range got {
		if a == "10.0.1.5" {
			t.Fatal("collected our own address as a peer")
		}
	}
}

// TestASecondWindowAsksAWSNothing: in daemon mode the same internal services
// are reached every hour, and what an ENI is called changes on the order of
// never. Without a cache the collector re-asks the same question forever, on
// an API whose rate limit it shares with the customer's own tooling.
func TestASecondWindowAsksAWSNothing(t *testing.T) {
	fake := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		destEni("10.0.4.23", "ELB app/billing-api/50dc"),
	}}
	r := &DestinationResolver{API: fake}

	first, err := r.Resolve(context.Background(), []string{"10.0.4.23"})
	if err != nil || len(first) != 1 {
		t.Fatalf("first resolve: %v, %v", first, err)
	}
	calls := fake.describes

	second, err := r.Resolve(context.Background(), []string{"10.0.4.23"})
	if err != nil {
		t.Fatal(err)
	}
	if fake.describes != calls {
		t.Fatalf("asked again: %d calls became %d", calls, fake.describes)
	}
	if len(second) != 1 || second[0].Name != "billing-api" {
		t.Fatalf("cache returned %v", second)
	}
}

// TestAnUnnamedAddressIsAlsoRemembered: untagged ENIs are the common case, and
// re-asking about every one of them every window is most of the cost the cache
// exists to avoid.
func TestAnUnnamedAddressIsAlsoRemembered(t *testing.T) {
	fake := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		destEni("10.0.4.23", "somebody's note about an incident"),
	}}
	r := &DestinationResolver{API: fake}

	if _, err := r.Resolve(context.Background(), []string{"10.0.4.23"}); err != nil {
		t.Fatal(err)
	}
	calls := fake.describes
	if _, err := r.Resolve(context.Background(), []string{"10.0.4.23"}); err != nil {
		t.Fatal(err)
	}
	if fake.describes != calls {
		t.Fatalf("re-asked about an address it could not name: %d -> %d", calls, fake.describes)
	}
}

// TestTheCacheCanBeTurnedOff: a negative TTL disables it, which is what
// someone debugging why a service is not being named wants — and it is how
// this test avoids sleeping to prove an entry expires.
func TestTheCacheCanBeTurnedOff(t *testing.T) {
	fake := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		destEni("10.0.4.23", "ELB app/billing-api/50dc"),
	}}
	r := &DestinationResolver{API: fake, TTL: -time.Second}

	if _, err := r.Resolve(context.Background(), []string{"10.0.4.23"}); err != nil {
		t.Fatal(err)
	}
	calls := fake.describes
	if _, err := r.Resolve(context.Background(), []string{"10.0.4.23"}); err != nil {
		t.Fatal(err)
	}
	if fake.describes == calls {
		t.Fatal("a disabled cache was still used")
	}
}

// TestOnlyTheUnknownAddressesAreAsked: a window that reaches one new service
// alongside twenty known ones should cost one address, not twenty-one.
func TestOnlyTheUnknownAddressesAreAsked(t *testing.T) {
	fake := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		destEni("10.0.4.23", "ELB app/billing-api/50dc"),
		destEni("10.0.4.24", "ELB app/orders-api/60ef"),
	}}
	r := &DestinationResolver{API: fake}

	if _, err := r.Resolve(context.Background(), []string{"10.0.4.23"}); err != nil {
		t.Fatal(err)
	}
	got, err := r.Resolve(context.Background(), []string{"10.0.4.23", "10.0.4.24"})
	if err != nil {
		t.Fatal(err)
	}
	// Both come back; only one was asked about the second time.
	if len(got) != 2 {
		t.Fatalf("lost a cached entry: %v", got)
	}
	if got[0].Address != "10.0.4.23" || got[1].Address != "10.0.4.24" {
		t.Fatalf("cached and fresh results were not merged in order: %v", got)
	}
}

// TestManagedInterfacesAreNamedByTheirType: InterfaceType is a closed enum AWS
// sets itself, present on exactly the interfaces nobody tags. Every account has
// a NAT gateway and none of them has ever had a Name tag.
func TestManagedInterfacesAreNamedByTheirType(t *testing.T) {
	for _, tc := range []struct {
		kind ec2types.NetworkInterfaceType
		want string
	}{
		{ec2types.NetworkInterfaceTypeNatGateway, "nat-gateway/nat-gateway"},
		{ec2types.NetworkInterfaceTypeTransitGateway, "transit-gateway/transit-gateway"},
		{ec2types.NetworkInterfaceTypeNetworkLoadBalancer, "network-load-balancer/load-balancer"},
		{ec2types.NetworkInterfaceTypeApiGatewayManaged, "api-gateway/api-gateway"},
		{ec2types.NetworkInterfaceTypeGlobalAcceleratorManaged, "global-accelerator/global-accelerator"},
	} {
		iface := destEni("10.0.0.9", "")
		iface.InterfaceType = tc.kind
		names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.0.9")
		if names["10.0.0.9"] != tc.want {
			t.Errorf("%s: got %q, want %q", tc.kind, names["10.0.0.9"], tc.want)
		}
	}
}

// TestAnOrdinaryInterfaceTypeNamesNothing: `interface`, `branch` and `trunk`
// say only that something made an ENI in the ordinary way. Putting a word in
// front of an approver that carries no information is worse than the address
// it replaced.
func TestAnOrdinaryInterfaceTypeNamesNothing(t *testing.T) {
	for _, kind := range []ec2types.NetworkInterfaceType{
		ec2types.NetworkInterfaceTypeInterface,
		ec2types.NetworkInterfaceTypeBranch,
		ec2types.NetworkInterfaceTypeTrunk,
		ec2types.NetworkInterfaceTypeEfa,
	} {
		iface := destEni("10.0.11.8", "")
		iface.InterfaceType = kind
		if names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.11.8"); len(names) != 0 {
			t.Errorf("%s was named %v", kind, names)
		}
	}
}

// TestATagStillWinsOverTheInterfaceType: what a customer called the thing
// beats what AWS calls the plumbing. An NLB somebody named `checkout-lb` is
// `checkout-lb`, because that is the word their runbook uses.
func TestATagStillWinsOverTheInterfaceType(t *testing.T) {
	iface := destEni("10.0.3.9", "", "Name", "checkout-lb")
	iface.InterfaceType = ec2types.NetworkInterfaceTypeNetworkLoadBalancer
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.3.9")
	if names["10.0.3.9"] != "checkout-lb/tag" {
		t.Fatalf("got %v", names)
	}
}

// TestADescriptionStillWinsOverTheInterfaceType: `billing-api` is a better
// answer than `load-balancer`, and AWS wrote both.
func TestADescriptionStillWinsOverTheInterfaceType(t *testing.T) {
	iface := destEni("10.0.4.23", "ELB app/billing-api/50dc6c495c0c9188")
	iface.InterfaceType = ec2types.NetworkInterfaceTypeLoadBalancer
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.4.23")
	if names["10.0.4.23"] != "billing-api/load-balancer" {
		t.Fatalf("got %v", names)
	}
}

// TestFreeTextStartingWithAKnownShapeIsNotParsed: SEC-23. The shapes are
// anchored at both ends, so a person who began a note with "Amazon EKS" does
// not have the rest of their sentence read as a cluster name.
func TestFreeTextStartingWithAKnownShapeIsNotParsed(t *testing.T) {
	for _, description := range []string{
		"Amazon EKS cluster - ask Sam before deleting, see INC-4471",
		"EFS mount target for fs-0a1b2c3d (fsmt-0e4f) DO NOT DELETE",
		"Interface for NAT Gateway nat-0a1b2c3d (old one, being retired)",
		"aws-K8S-i-0a1b2c3d4e5f60718 temp",
	} {
		iface := destEni("10.0.11.7", description)
		if names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.11.7"); len(names) != 0 {
			t.Errorf("%q was parsed as %v", description, names)
		}
	}
}

// TestTheFilesystemIdIsNotCarried: `fs-0a1b2c3d` is not a name anybody
// recognises, and an approver deciding about shared storage is deciding about
// shared storage.
func TestTheFilesystemIdIsNotCarried(t *testing.T) {
	iface := destEni("10.0.7.11", "EFS mount target for fs-0a1b2c3d (fsmt-0e4f)")
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.7.11")
	if names["10.0.7.11"] != "efs/efs" {
		t.Fatalf("got %v", names)
	}
}

func groupedEni(address string, groups ...string) ec2types.NetworkInterface {
	iface := destEni(address, "")
	for _, name := range groups {
		iface.Groups = append(iface.Groups, ec2types.GroupIdentifier{
			GroupId: aws.String("sg-" + name), GroupName: aws.String(name),
		})
	}
	return iface
}

// TestTheOneGroupSomebodyNamedNamesTheInterface: nobody tags an ENI — the
// console does not show the field during instance launch — and almost
// everybody names a security group, because the console makes them type one.
func TestTheOneGroupSomebodyNamedNamesTheInterface(t *testing.T) {
	names := resolveNames(t,
		[]ec2types.NetworkInterface{groupedEni("10.0.4.50", "orders-service-sg")},
		"10.0.4.50")
	if names["10.0.4.50"] != "orders-service/security-group" {
		t.Fatalf("got %v", names)
	}
}

// TestAGeneratedGroupNameNamesNothing: `default` is what AWS called it and
// `launch-wizard-3` is what the console called it. Neither is what anybody
// called the service.
func TestAGeneratedGroupNameNamesNothing(t *testing.T) {
	for _, name := range []string{
		"default",
		"launch-wizard-3",
		"eks-cluster-sg-prod-1029384756",
		"k8s-elb-a1b2c3d4e5",
		"checkout-stack-AppSecurityGroup-1A2B3C4D5E6F",
	} {
		iface := groupedEni("10.0.4.51", name)
		if got := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.4.51"); len(got) != 0 {
			t.Errorf("%q named the interface %v", name, got)
		}
	}
}

// TestTwoNamedGroupsNameNothing: two groups are two claims about what this is,
// and picking one would be choosing which of a customer's two answers to put
// in front of an approver.
func TestTwoNamedGroupsNameNothing(t *testing.T) {
	iface := groupedEni("10.0.4.52", "orders-service-sg", "shared-egress")
	if got := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.4.52"); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

// TestAGeneratedGroupDoesNotCountAsASecondClaim: an interface in its service's
// group and the VPC default is in one group anybody chose.
func TestAGeneratedGroupDoesNotCountAsASecondClaim(t *testing.T) {
	iface := groupedEni("10.0.4.53", "orders-service-sg", "default")
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.4.53")
	if names["10.0.4.53"] != "orders-service/security-group" {
		t.Fatalf("got %v", names)
	}
}

// TestAGroupNameLosesToEverythingElse: it is the weakest source in the list.
func TestAGroupNameLosesToEverythingElse(t *testing.T) {
	tagged := groupedEni("10.0.4.54", "orders-service-sg")
	tagged.TagSet = []ec2types.Tag{{Key: aws.String("Name"), Value: aws.String("orders-api")}}
	names := resolveNames(t, []ec2types.NetworkInterface{tagged}, "10.0.4.54")
	if names["10.0.4.54"] != "orders-api/tag" {
		t.Fatalf("got %v", names)
	}

	managed := groupedEni("10.0.4.55", "orders-service-sg")
	managed.InterfaceType = ec2types.NetworkInterfaceTypeNatGateway
	names = resolveNames(t, []ec2types.NetworkInterface{managed}, "10.0.4.55")
	if names["10.0.4.55"] != "nat-gateway/nat-gateway" {
		t.Fatalf("got %v", names)
	}
}

// TestANameTagThatIsAnIdentifierIsNotAName: tooling writes the Name field too,
// and what it writes is an id — honest, and exactly as useful to an approver
// as the address it would replace.
func TestANameTagThatIsAnIdentifierIsNotAName(t *testing.T) {
	for _, name := range []string{
		"i-0a1b2c3d4e5f60718",
		"eni-0a1b2c3d4e5f60718",
		"tf-20260814093211004500000003",
		"vol-0a1b2c3d",
	} {
		iface := destEni("10.0.12.1", "", "Name", name)
		if got := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.12.1"); len(got) != 0 {
			t.Errorf("%q was used as a name: %v", name, got)
		}
	}
}

// TestARealNameIsNotMistakenForAnIdentifier: the rule is a hyphen, a short
// lowercase prefix, and eight or more hex digits. Service names are not that
// and must not be caught by it.
func TestARealNameIsNotMistakenForAnIdentifier(t *testing.T) {
	for _, name := range []string{
		"billing-api", "orders-service", "checkout-v2", "db-primary",
		"api-gateway-edge", "svc-payments", "eu-billing-2026",
	} {
		iface := destEni("10.0.4.21", "", "Name", name)
		got := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.4.21")
		if got["10.0.4.21"] != name+"/tag" {
			t.Errorf("%q was rejected: %v", name, got)
		}
	}
}

// TestAnIdentifierNameFallsThroughToTheRest: rejecting it is not the end of
// the search. An instance tagged with its own id and sitting in a group
// somebody named is still nameable.
func TestAnIdentifierNameFallsThroughToTheRest(t *testing.T) {
	iface := groupedEni("10.0.12.5", "orders-service-sg")
	iface.TagSet = []ec2types.Tag{
		{Key: aws.String("Name"), Value: aws.String("i-0a1b2c3d4e5f60718")},
	}
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.12.5")
	if names["10.0.12.5"] != "orders-service/security-group" {
		t.Fatalf("got %v", names)
	}
}

// TestAServiceNameAwsWroteIsUsed: `aws:` is a reserved tag prefix. A customer
// cannot create a tag in it — AWS rejects the call — so these are AWS's own
// metadata rather than a label somebody typed.
func TestAServiceNameAwsWroteIsUsed(t *testing.T) {
	iface := destEni("10.0.13.1", "arn:aws:ecs:us-east-1:1:attachment/9c8f",
		"aws:ecs:serviceName", "checkout", "aws:ecs:clusterName", "prod")
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.13.1")
	if names["10.0.13.1"] != "checkout/ecs" {
		t.Fatalf("got %v", names)
	}
}

// TestAClusterIsNotAService: a standalone task carries the cluster tag and no
// service tag. Naming it after the cluster would give every standalone task in
// the account the same name, which is worse than an address because it looks
// like an answer.
func TestAClusterIsNotAService(t *testing.T) {
	iface := destEni("10.0.13.2", "arn:aws:ecs:us-east-1:1:attachment/1a2b",
		"aws:ecs:clusterName", "prod")
	if got := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.13.2"); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

// TestTheMostSpecificManagedTagWins: an interface can carry several. A task in
// a service is that service.
func TestTheMostSpecificManagedTagWins(t *testing.T) {
	iface := destEni("10.0.13.3", "",
		"cluster.k8s.amazonaws.com/name", "prod-agents",
		"aws:ecs:serviceName", "checkout")
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.13.3")
	if names["10.0.13.3"] != "checkout/ecs" {
		t.Fatalf("got %v", names)
	}
}

// TestANameTagStillWinsOverWhatAwsWrote: what a customer called the thing
// beats what the platform that launched it calls the launch.
func TestANameTagStillWinsOverWhatAwsWrote(t *testing.T) {
	iface := destEni("10.0.13.4", "", "Name", "checkout-api", "aws:ecs:serviceName", "checkout")
	names := resolveNames(t, []ec2types.NetworkInterface{iface}, "10.0.13.4")
	if names["10.0.13.4"] != "checkout-api/tag" {
		t.Fatalf("got %v", names)
	}
}

// TestAnInterfaceIsNamedAfterTheInstanceBehindIt: the common case in a real
// account. The console shows a Name field when launching an instance and does
// not show one for the interface it creates, so an account with careful tag
// hygiene still has ENIs named nothing at all.
func TestAnInterfaceIsNamedAfterTheInstanceBehindIt(t *testing.T) {
	iface := destEni("10.0.14.1", "")
	iface.Attachment = &ec2types.NetworkInterfaceAttachment{InstanceId: aws.String("i-0a1b")}
	names := resolveNamesWith(t, &fakeEC2{
		interfaces:    []ec2types.NetworkInterface{iface},
		instanceNames: map[string]string{"i-0a1b": "checkout-api"},
	}, "10.0.14.1")
	if names["10.0.14.1"] != "checkout-api/instance" {
		t.Fatalf("got %v", names)
	}
}

// TestTheInstanceIsOnlyAskedAboutWhenNothingElseAnswered: the call is free of
// new permissions but not free of quota. An account with tidy ENIs must never
// pay for it.
func TestTheInstanceIsOnlyAskedAboutWhenNothingElseAnswered(t *testing.T) {
	tagged := destEni("10.0.4.21", "", "Name", "billing-api")
	tagged.Attachment = &ec2types.NetworkInterfaceAttachment{InstanceId: aws.String("i-0a1b")}
	api := &fakeEC2{
		interfaces:    []ec2types.NetworkInterface{tagged},
		instanceNames: map[string]string{"i-0a1b": "something-else"},
	}
	names := resolveNamesWith(t, api, "10.0.4.21")

	if names["10.0.4.21"] != "billing-api/tag" {
		t.Fatalf("got %v", names)
	}
	if api.describedInstances != 0 {
		t.Fatalf("asked about the instance %d times when the ENI was named",
			api.describedInstances)
	}
}

// TestAnInstanceTaggedWithItsOwnIdIsNotAName: the same rule one level up.
func TestAnInstanceTaggedWithItsOwnIdIsNotAName(t *testing.T) {
	iface := destEni("10.0.14.2", "")
	iface.Attachment = &ec2types.NetworkInterfaceAttachment{InstanceId: aws.String("i-0b2c")}
	names := resolveNamesWith(t, &fakeEC2{
		interfaces:    []ec2types.NetworkInterface{iface},
		instanceNames: map[string]string{"i-0b2c": "i-0b2c3d4e5f6071829"},
	}, "10.0.14.2")
	if len(names) != 0 {
		t.Fatalf("got %v", names)
	}
}

// TestAnInterfaceWithNoInstanceIsNotAskedAbout: a NAT gateway, a load balancer
// and a VPC endpoint all have attachments that name no instance, and they were
// named by their type before this ran.
func TestAnInterfaceWithNoInstanceIsNotAskedAbout(t *testing.T) {
	api := &fakeEC2{interfaces: []ec2types.NetworkInterface{destEni("10.0.11.8", "")}}
	resolveNamesWith(t, api, "10.0.11.8")
	if api.describedInstances != 0 {
		t.Fatalf("asked about an instance that does not exist")
	}
}

// TestAnInterfaceEndpointIsNamedAfterItsService: an account reaching Bedrock
// over PrivateLink sends every model call to a private address in its own
// subnet, with nothing in the flow record to say so. This is the one read that
// can tell us, and the agents behind it are invisible without it.
func TestAnInterfaceEndpointIsNamedAfterItsService(t *testing.T) {
	iface := destEni("10.0.15.20", "VPC Endpoint Interface vpce-0b3d5f7a")
	names := resolveNamesWith(t, &fakeEC2{
		interfaces: []ec2types.NetworkInterface{iface},
		endpoints: map[string]string{
			"vpce-0b3d5f7a": "com.amazonaws.us-east-1.bedrock-runtime",
		},
	}, "10.0.15.20")
	if names["10.0.15.20"] != "bedrock-runtime/vpc-endpoint" {
		t.Fatalf("got %v", names)
	}
}

// TestTheEndpointServiceTravels: the name is for the operator; the service is
// what lets the classifier see the model traffic that is there.
func TestTheEndpointServiceTravels(t *testing.T) {
	iface := destEni("10.0.15.20", "VPC Endpoint Interface vpce-0b3d5f7a")
	r := &DestinationResolver{API: &fakeEC2{
		interfaces: []ec2types.NetworkInterface{iface},
		endpoints: map[string]string{
			"vpce-0b3d5f7a": "com.amazonaws.us-east-1.bedrock-runtime",
		},
	}}
	got, err := r.Resolve(context.Background(), []string{"10.0.15.20"})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Service != "com.amazonaws.us-east-1.bedrock-runtime" {
		t.Fatalf("got %+v", got)
	}
}

// TestAnEndpointWeCannotResolveKeepsItsId: the endpoint id is what the
// description gave it, and it is what the register showed before this existed.
// A failure here must not cost the customer a name they already had.
func TestAnEndpointWeCannotResolveKeepsItsId(t *testing.T) {
	iface := destEni("10.0.5.30", "VPC Endpoint Interface vpce-0a1b2c3d")
	names := resolveNamesWith(t, &fakeEC2{
		interfaces: []ec2types.NetworkInterface{iface},
	}, "10.0.5.30")
	if names["10.0.5.30"] != "vpce-0a1b2c3d/vpc-endpoint" {
		t.Fatalf("got %v", names)
	}
}

// TestEndpointsAreOnlyAskedAboutWhenThereAreSome: an account with no interface
// endpoints must not pay a call for the ones it does not have.
func TestEndpointsAreOnlyAskedAboutWhenThereAreSome(t *testing.T) {
	api := &fakeEC2{interfaces: []ec2types.NetworkInterface{
		destEni("10.0.4.21", "", "Name", "billing-api"),
	}}
	resolveNamesWith(t, api, "10.0.4.21")
	if api.describedEndpoints != 0 {
		t.Fatalf("asked about endpoints %d times on an account with none",
			api.describedEndpoints)
	}
}

// TestAPrivateLinkServiceSomebodyElsePublishedKeepsItsId: there is no service
// segment to read, and inventing one would be a claim about somebody else's
// account.
func TestAPrivateLinkServiceSomebodyElsePublishedKeepsItsId(t *testing.T) {
	iface := destEni("10.0.15.21", "VPC Endpoint Interface vpce-0c4e6a8b")
	names := resolveNamesWith(t, &fakeEC2{
		interfaces: []ec2types.NetworkInterface{iface},
		endpoints: map[string]string{
			"vpce-0c4e6a8b": "com.amazonaws.vpce.us-east-1.vpce-svc-0a1b2c3d",
		},
	}, "10.0.15.21")
	if names["10.0.15.21"] != "vpce-svc-0a1b2c3d/vpc-endpoint" {
		t.Fatalf("got %v", names)
	}
}

// TestTheCacheIsBounded: the cache used to live for one collection, so its
// size was the size of one window. It lives for the life of the daemon now,
// and an account with churn — ECS tasks get a new address every deploy — adds
// entries for ever without a bound.
func TestTheCacheIsBounded(t *testing.T) {
	r := &DestinationResolver{API: &fakeEC2{}}
	for i := range MaxCached + 100 {
		r.remember(fmt.Sprintf("10.%d.%d.%d", i/65536, (i/256)%256, i%256),
			wire.Destination{}, false)
	}
	if len(r.cache) > MaxCached {
		t.Fatalf("cache holds %d entries, bound is %d", len(r.cache), MaxCached)
	}
}

// TestASweepKeepsWhatIsStillLive: eviction is a sweep of expired entries, not
// an LRU. What is worth keeping is what is still inside its TTL.
func TestASweepKeepsWhatIsStillLive(t *testing.T) {
	r := &DestinationResolver{API: &fakeEC2{}, TTL: time.Hour}
	r.cache = map[string]cached{
		"10.0.0.1": {at: time.Now().Add(-2 * time.Hour)},
		"10.0.0.2": {at: time.Now()},
	}
	r.evictExpired(time.Now())

	if _, ok := r.cache["10.0.0.1"]; ok {
		t.Fatal("an expired entry survived the sweep")
	}
	if _, ok := r.cache["10.0.0.2"]; !ok {
		t.Fatal("a live entry was swept")
	}
}

// TestAFullCacheOfLiveEntriesIsDropped: the alternative is a resolver that has
// reached its limit with live entries and stops caching anything new for ever
// — the same as no cache, except silent and holding 50,000 entries.
func TestAFullCacheOfLiveEntriesIsDropped(t *testing.T) {
	r := &DestinationResolver{API: &fakeEC2{}, TTL: time.Hour}
	r.cache = map[string]cached{}
	for i := range MaxCached {
		r.cache[fmt.Sprintf("10.%d.%d.%d", i/65536, (i/256)%256, i%256)] =
			cached{at: time.Now()}
	}
	r.evictExpired(time.Now())

	if len(r.cache) != 0 {
		t.Fatalf("a full cache of live entries kept %d", len(r.cache))
	}
}
