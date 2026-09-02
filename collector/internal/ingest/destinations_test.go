package ingest

import (
	"context"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"
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
	r := &DestinationResolver{API: &fakeEC2{interfaces: ifaces}}
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
