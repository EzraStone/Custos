package ingest

import (
	"fmt"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"
)

// An account's worth of network interfaces, shaped the way AWS returns them.
//
// The register's scope is what an operator reads before conferring authority,
// and an entry they cannot read is an approval they have to guess at.
// docs/STATUS.md has said since A0 that how much of a real scope is readable
// is unmeasured — "in the corpus that is one endpoint out of seven; in a real
// account the ratio depends entirely on whether the customer tags ENIs, and
// nobody has measured it."
//
// That is the same failure the gateway questions had: a number that decides
// whether a human can act, with nothing measuring it. So this is an estate
// rather than a set of cases. Every entry is a shape AWS actually returns,
// with the tag hygiene a company that adopted agents bottom-up actually has:
// a few things carefully labelled, a lot of managed infrastructure carrying
// AWS's own descriptions, and a long tail of interfaces nobody has ever named.
//
// `want` is what a human should be able to read. Empty means there is nothing
// honest to say and the address is the right answer.
type estateEni struct {
	iface ec2types.NetworkInterface
	want  string
	// instance is what the instance behind this interface is called, when
	// there is one and somebody named it. Interfaces are tagged far less often
	// than the instances they are attached to, and this is what that costs.
	instance string
	// absent means AWS returns no interface for this address. A shared VPC
	// owned by another account, or an on-prem host over Direct Connect: the
	// address is reached, the role can see nothing about it, and the honest
	// answer is the address.
	absent bool
	// service is the AWS endpoint service this interface is an endpoint for,
	// when it is one. The field that turns a private address into a model
	// endpoint.
	service string
	why     string
}

func withType(i ec2types.NetworkInterface, kind ec2types.NetworkInterfaceType) ec2types.NetworkInterface {
	i.InterfaceType = kind
	return i
}

func withGroups(i ec2types.NetworkInterface, names ...string) ec2types.NetworkInterface {
	for _, name := range names {
		i.Groups = append(i.Groups, ec2types.GroupIdentifier{
			GroupId:   aws.String("sg-" + name),
			GroupName: aws.String(name),
		})
	}
	return i
}

func attachedTo(i ec2types.NetworkInterface, instance string) ec2types.NetworkInterface {
	i.Attachment = &ec2types.NetworkInterfaceAttachment{InstanceId: aws.String(instance)}
	return i
}

func withRequester(i ec2types.NetworkInterface, who string) ec2types.NetworkInterface {
	i.RequesterId = aws.String(who)
	i.RequesterManaged = aws.Bool(true)
	return i
}

// estate is the corpus. Addresses are unique so a resolver can be run over the
// whole of it in one call.
func estate() []estateEni {
	return []estateEni{
		// --- labelled by somebody who cared ---------------------------------
		{
			iface: destEni("10.0.4.21", "", "Name", "billing-api"),
			want:  "billing-api",
			why:   "the best case, and the one every demo account is built from",
		},
		{
			iface: destEni("10.0.4.22", "", "Name", "ticketing", "env", "prod"),
			want:  "ticketing",
			why:   "a Name tag among others",
		},

		// --- AWS wrote the description --------------------------------------
		{
			iface: destEni("10.0.4.23", "ELB app/billing-api/50dc6c495c0c9188"),
			want:  "billing-api",
			why:   "an application load balancer names itself",
		},
		{
			iface: destEni("10.0.9.44", "RDSNetworkInterface"),
			want:  "rds",
			why:   "RDS says what it is and not which instance; the address carries that",
		},
		{
			iface: destEni("10.0.9.60", "ElastiCache redis-prod-001"),
			want:  "elasticache",
			why:   "same shape as RDS",
		},
		{
			iface: destEni("10.0.5.30", "VPC Endpoint Interface vpce-0a1b2c3d"),
			want:  "vpce-0a1b2c3d",
			why:   "a VPC endpoint, which is how an account reaches S3 privately",
		},
		{
			iface: destEni("10.0.6.15",
				"AWS Lambda VPC ENI-checkout-worker-f7a1b2c3-4d5e-6f70-8a9b-0c1d2e3f4a5b"),
			want: "checkout-worker",
			why:  "a Lambda in a VPC. The suffix is a full UUID, which is what anchors the parse",
		},

		{
			iface: destEni("10.0.5.80", "RedshiftNetworkInterface"),
			want:  "redshift",
			why:   "a warehouse, and a plausible thing for an agent to be querying",
		},
		{
			iface: destEni("10.0.5.81", "Amazon EKS prod-agents"),
			want:  "prod-agents",
			why:   "the cluster control plane, named after the cluster",
		},
		{
			iface: destEni("10.0.5.82", "aws-K8S-i-0a1b2c3d4e5f60718"),
			want:  "eks-node",
			why:   "a pod ENI from the CNI plugin. Node level is where EKS attribution stops anyway",
		},
		{
			iface: destEni("10.0.0.10", "Interface for NAT Gateway nat-0a1b2c3d"),
			want:  "nat-gateway",
			why:   "the description AWS wrote before the interface type existed",
		},

		// --- structured, and currently thrown away --------------------------
		{
			iface: withType(destEni("10.0.0.9", ""), ec2types.NetworkInterfaceTypeNatGateway),
			want:  "nat-gateway",
			why:   "a NAT gateway has no tags and no description, and every account has one",
		},
		{
			iface: destEni("10.0.7.11", "EFS mount target for fs-0a1b2c3d (fsmt-0e4f)"),
			want:  "efs",
			why:   "a shared filesystem. AWS gives these the plain `interface` type, so the description is the only signal",
		},
		{
			iface: withType(destEni("10.0.3.4", ""), ec2types.NetworkInterfaceTypeTransitGateway),
			want:  "transit-gateway",
			why:   "traffic leaving for another VPC or account",
		},
		{
			iface: withType(destEni("10.0.3.9", ""), ec2types.NetworkInterfaceTypeNetworkLoadBalancer),
			want:  "network-load-balancer",
			why:   "an NLB carries no ELB description, unlike an ALB",
		},
		{
			iface: withType(destEni("10.0.8.40", ""), ec2types.NetworkInterfaceTypeApiGatewayManaged),
			want:  "api-gateway",
			why:   "a private API Gateway endpoint",
		},

		// --- named by the only label anybody gave it ------------------------
		{
			iface: withGroups(destEni("10.0.4.50", ""), "orders-service-sg"),
			want:  "orders-service",
			why:   "nobody tagged the ENI and somebody named the security group",
		},
		{
			iface: withGroups(destEni("10.0.4.51", ""), "default"),
			want:  "",
			why:   "`default` is what AWS called it, not what anybody called the service",
		},
		{
			iface: withGroups(destEni("10.0.4.52", ""), "launch-wizard-3"),
			want:  "",
			why:   "the console picked that name; it says nothing about the service",
		},
		{
			iface: withGroups(destEni("10.0.4.53", ""), "eks-cluster-sg-prod-1029384756"),
			want:  "",
			why:   "an EKS-managed group; the cluster name is in it but so is a node's worth of noise",
		},

		// --- labelled by a machine, which is not the same as labelled -------
		{
			iface: destEni("10.0.12.1", "", "Name", "i-0a1b2c3d4e5f60718"),
			want:  "",
			why:   "a Name tag holding the instance id. As readable as the address it replaced",
		},
		{
			iface: destEni("10.0.12.2", "", "Name", "eni-0a1b2c3d4e5f60718"),
			want:  "",
			why:   "tooling that tagged the ENI with its own id",
		},
		{
			iface: destEni("10.0.12.3", "", "Name", "tf-20260814093211004500000003"),
			want:  "",
			why:   "Terraform's generated name prefix. A name nobody chose and nobody reads",
		},
		{
			iface: destEni("10.0.12.4", "", "name", "checkout-api"),
			want:  "",
			why:   "lowercase `name`. Not the key the console writes, and guessing at tag keys is how a note ends up in a scope",
		},

		// --- labelled by AWS, on the ENI ------------------------------------
		{
			iface: destEni("10.0.13.1", "arn:aws:ecs:us-east-1:447120043318:attachment/9c8f",
				"aws:ecs:serviceName", "checkout", "aws:ecs:clusterName", "prod"),
			want: "checkout",
			why:  "ECS in awsvpc mode tags the ENI with the service it belongs to",
		},
		{
			iface: destEni("10.0.13.2", "arn:aws:ecs:us-east-1:447120043318:attachment/1a2b",
				"aws:ecs:clusterName", "prod"),
			want: "",
			why:  "a standalone ECS task. The cluster is not the service, and every task in it would share the name",
		},

		// --- tagged on the instance, not the interface ----------------------
		{
			iface:    attachedTo(destEni("10.0.14.1", ""), "i-0a1b2c3d4e5f60718"),
			want:     "checkout-api",
			instance: "checkout-api",
			why:      "THE COMMON CASE. People tag instances, not interfaces, and the ENI carries only the attachment",
		},
		{
			iface:    attachedTo(destEni("10.0.14.2", ""), "i-0b2c3d4e5f6071829"),
			want:     "",
			instance: "i-0b2c3d4e5f6071829",
			why:      "an instance tagged with its own id. The same rule applies one level up",
		},
		{
			iface: attachedTo(destEni("10.0.14.3", ""), "i-0c3d4e5f60718293a"),
			want:  "",
			why:   "an instance nobody tagged either. The bottom of the search",
		},

		// --- an endpoint, and what is on the other side of it ---------------
		{
			iface:   destEni("10.0.15.20", "VPC Endpoint Interface vpce-0b3d5f7a"),
			want:    "bedrock-runtime",
			service: "com.amazonaws.us-east-1.bedrock-runtime",
			why:     "model traffic that never leaves the VPC. Every agent behind it is invisible without this",
		},
		{
			iface: destEni("10.0.5.31", "VPC Endpoint Interface vpce-0f9e8d7c"),
			want:  "vpce-0f9e8d7c",
			why:   "an endpoint AWS would not tell us about. The id is what the description gave it",
		},

		// --- labelled by a person, in a field that takes anything -----------
		{
			iface: destEni("10.0.16.1", "", "Name", "billing\tapi  (prod)"),
			want:  "billing api (prod)",
			why:   "a tag value is text somebody typed; a tab in it breaks a column-aligned scope",
		},
		{
			iface: destEni("10.0.16.2", "", "Name",
				"a-very-long-service-name-that-somebody-pasted-in-from-a-runbook-and-never-shortened"),
			want: "a-very-long-service-name-that-somebody-pasted-in-from-a-runbook-\u2026",
			why:  "256 characters is a legal tag value and would push the address off the line",
		},

		// --- nothing honest to say ------------------------------------------
		{
			iface:  destEni("10.0.16.9", ""),
			absent: true,
			want:   "",
			why:    "AN ADDRESS WITH NO INTERFACE IN THIS ACCOUNT. A shared-services VPC owned by another account, or on-prem over Direct Connect. Nothing one account's role can answer",
		},
		{
			iface: destEni("10.0.11.7", "temp box for INC-4471, ask Sam before deleting"),
			want:  "",
			why:   "SEC-23: free text a person typed, and exactly the kind that must not leave",
		},
		{
			iface: destEni("10.0.11.8", ""),
			want:  "",
			why:   "an interface with no tags, no description and no groups. The long tail",
		},
		{
			iface: withRequester(destEni("10.0.11.9", ""), "amazon-ecs"),
			want:  "",
			why:   "requester-managed and unlabelled; knowing ECS made it does not say which service",
		},
	}
}

// TestTheEstateHasTheTagHygieneItClaims: a corpus that names everything
// measures nothing. Two in five of these have no honest answer, which is the
// point — the number this produces is only meaningful if the unnameable cases
// are in it.
func TestTheEstateHasTheTagHygieneItClaims(t *testing.T) {
	all := estate()
	unnameable := 0
	seen := map[string]bool{}
	for _, e := range all {
		if e.want == "" {
			unnameable++
		}
		address := *e.iface.PrivateIpAddresses[0].PrivateIpAddress
		if seen[address] {
			t.Fatalf("%s appears twice; the estate is resolved in one call", address)
		}
		seen[address] = true
		if e.why == "" {
			t.Fatalf("%s has no reason attached", address)
		}
	}
	if unnameable < len(all)/4 {
		t.Fatalf("only %d of %d interfaces are unnameable; the estate is too tidy",
			unnameable, len(all))
	}
}

// readableFloor is how many of the estate's nameable interfaces the resolver
// gets right today. It is a floor rather than a target: the measurement exists
// to make the number move deliberately, and a test that only checks a
// threshold somebody picked stops meaning anything the moment it is met.
//
//	6 of 13 — the Name tag and four of the five AWS description shapes
const readableFloor = 23

// TestScopeReadability is the measurement, and the only number in this package
// that a customer feels directly. An entry an operator cannot read is an
// approval they have to guess at, and the register's scope is what they read
// before conferring authority.
func TestScopeReadability(t *testing.T) {
	all := estate()
	var addresses []string
	var ifaces []ec2types.NetworkInterface
	for _, e := range all {
		if !e.absent {
			ifaces = append(ifaces, e.iface)
		}
		addresses = append(addresses, *e.iface.PrivateIpAddresses[0].PrivateIpAddress)
	}

	instanceNames := map[string]string{}
	endpoints := map[string]string{}
	for _, e := range all {
		if e.instance != "" {
			instanceNames[*e.iface.Attachment.InstanceId] = e.instance
		}
		if e.service != "" {
			endpoints[endpointID(e.iface)] = e.service
		}
	}
	names := resolveNamesWith(t, &fakeEC2{
		interfaces: ifaces, instanceNames: instanceNames, endpoints: endpoints,
	}, addresses...)

	var wrong, missing []string
	readable, nameable := 0, 0
	for _, e := range all {
		address := *e.iface.PrivateIpAddresses[0].PrivateIpAddress
		got := strings.SplitN(names[address], "/", 2)[0]
		if e.want != "" {
			nameable++
		}
		switch {
		case got == e.want && got != "":
			readable++
		case got == e.want:
			// Correctly unnamed. The address is the honest answer.
		case got == "":
			missing = append(missing, fmt.Sprintf("%s: %q (%s)", address, e.want, e.why))
		default:
			// A wrong name is worse than no name: it is an approval granted
			// against something the operator did not look at.
			wrong = append(wrong, fmt.Sprintf("%s: want %q, got %q (%s)",
				address, e.want, got, e.why))
		}
	}

	t.Logf("scope readability: %d of %d nameable interfaces (%.0f%%)",
		readable, nameable, 100*float64(readable)/float64(nameable))
	if len(wrong) > 0 {
		t.Errorf("named wrongly, which is worse than not naming:\n  %s",
			strings.Join(wrong, "\n  "))
	}
	if readable < readableFloor {
		t.Errorf("readability fell to %d of %d, from %d:\n  %s",
			readable, nameable, readableFloor, strings.Join(missing, "\n  "))
	}
	if readable > readableFloor {
		t.Errorf("readability rose to %d of %d; raise readableFloor and say why",
			readable, nameable)
	}
}
