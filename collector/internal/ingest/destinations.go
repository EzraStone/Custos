package ingest

import (
	"context"
	"fmt"
	"net/netip"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/service/ec2"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// DestinationResolver names the internal addresses a workload reached.
//
// Everything else in this package answers "who was this traffic from". This
// answers "what was it to", which is the half of the register an operator is
// actually asked to approve. Until it existed the scope said 10.0.4.23.
//
// One read-only call: DescribeNetworkInterfaces filtered by private address.
// The addresses come from flow logs the collector has already read, so this
// discovers nothing it was not already looking at (SEC-16).
type DestinationResolver struct {
	API awsread.NetworkAPI

	// TTL is how long a resolved name is reused before being asked again.
	// Zero means DefaultTTL; negative disables the cache entirely, which is
	// what someone debugging why a service is not being named wants.
	//
	// In daemon mode the same internal services are reached every window, and
	// what an ENI is called changes on the order of never. Without a cache the
	// collector re-asks AWS the same question hourly, forever, on an API whose
	// rate limit it shares with the customer's own tooling. With one, a steady
	// account costs a handful of calls a day.
	TTL time.Duration

	mu    sync.Mutex
	cache map[string]cached
}

type cached struct {
	destination wire.Destination
	at          time.Time
	// found records that AWS was asked and had nothing to say. Cached like any
	// other answer: an untagged ENI is the common case, and re-asking about
	// every unnamed address every window is most of the cost this avoids.
	found bool
}

// DefaultTTL is long enough that a steady account costs almost nothing and
// short enough that tagging an ENI shows up in the same working day.
const DefaultTTL = 6 * time.Hour

// describeChunk is the number of addresses per DescribeNetworkInterfaces call.
// The filter list is capped, and one call per address would be thousands of
// billable requests on an account of any size.
const describeChunk = 200

// Resolve names what it can and stays quiet about the rest.
//
// An address it cannot name is omitted rather than returned with an empty
// name: the control plane already knows how to show a bare address, and an
// entry asserting "this is called nothing" would be worse than no entry.
func (r *DestinationResolver) Resolve(ctx context.Context, addresses []string) ([]wire.Destination, error) {
	internal := internalOnly(addresses)
	if len(internal) == 0 {
		return nil, nil
	}

	out, ask := r.fromCache(internal)
	for start := 0; start < len(ask); start += describeChunk {
		end := min(start+describeChunk, len(ask))
		batch := ask[start:end]

		ifaces, err := r.describeByAddress(ctx, batch)
		if err != nil {
			// Naming is an improvement on the report, not a precondition for
			// it. A failure here must not cost the customer the scan, and what
			// was already resolved is still worth returning.
			sortByAddress(out)
			return out, err
		}

		resolved := map[string]wire.Destination{}
		for _, iface := range ifaces {
			name, kind := nameOf(iface)
			if name == "" {
				continue
			}
			for _, address := range privateAddresses(iface) {
				resolved[address] = wire.Destination{Address: address, Name: name, Kind: kind}
			}
		}

		// Every address in the batch gets a cache entry, including the ones
		// AWS had nothing to say about. Those are the common case and would
		// otherwise be re-asked every window forever.
		for _, address := range batch {
			d, ok := resolved[address]
			r.remember(address, d, ok)
			if ok {
				out = append(out, d)
			}
		}
	}
	sortByAddress(out)
	return out, nil
}

func sortByAddress(d []wire.Destination) {
	sort.Slice(d, func(i, j int) bool { return d[i].Address < d[j].Address })
}

// fromCache splits addresses into what is already known and what to ask about.
func (r *DestinationResolver) fromCache(addresses []string) (known []wire.Destination, ask []string) {
	if r.TTL < 0 {
		return nil, addresses
	}
	ttl := r.TTL
	if ttl == 0 {
		ttl = DefaultTTL
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	now := time.Now()
	for _, address := range addresses {
		entry, ok := r.cache[address]
		if !ok || now.Sub(entry.at) > ttl {
			ask = append(ask, address)
			continue
		}
		if entry.found {
			known = append(known, entry.destination)
		}
	}
	return known, ask
}

func (r *DestinationResolver) remember(address string, d wire.Destination, found bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.cache == nil {
		r.cache = map[string]cached{}
	}
	r.cache[address] = cached{destination: d, at: time.Now(), found: found}
}

func (r *DestinationResolver) describeByAddress(ctx context.Context, addresses []string) ([]ec2types.NetworkInterface, error) {
	var (
		all   []ec2types.NetworkInterface
		token *string
	)
	name := "addresses.private-ip-address"
	for {
		out, err := r.API.DescribeNetworkInterfaces(ctx, &ec2.DescribeNetworkInterfacesInput{
			Filters:   []ec2types.Filter{{Name: &name, Values: addresses}},
			NextToken: token,
		})
		if err != nil {
			return all, fmt.Errorf("describing destination interfaces: %w", err)
		}
		all = append(all, out.NetworkInterfaces...)
		if out.NextToken == nil || *out.NextToken == "" {
			return all, nil
		}
		token = out.NextToken
	}
}

// internalOnly keeps the addresses that could belong to an ENI in this account.
//
// A public address is never one, and asking about it wastes a filter slot on a
// call that is already paginated. It also keeps the request from carrying the
// addresses of third-party services the customer talks to, which is not
// information AWS needs from us to answer this question.
func internalOnly(addresses []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(addresses))
	for _, a := range addresses {
		if seen[a] {
			continue
		}
		addr, err := netip.ParseAddr(a)
		if err != nil || !addr.IsPrivate() {
			continue
		}
		seen[a] = true
		out = append(out, a)
	}
	sort.Strings(out)
	return out
}

func privateAddresses(iface ec2types.NetworkInterface) []string {
	var out []string
	for _, a := range iface.PrivateIpAddresses {
		if a.PrivateIpAddress != nil && *a.PrivateIpAddress != "" {
			out = append(out, *a.PrivateIpAddress)
		}
	}
	if len(out) == 0 && iface.PrivateIpAddress != nil {
		out = append(out, *iface.PrivateIpAddress)
	}
	return out
}

// AWS writes structured descriptions for the ENIs its managed services create.
// Matching them is how a destination gets a name that means something without
// forwarding whatever a customer typed into a description field.
var (
	elbDescription    = regexp.MustCompile(`^ELB (?:app|net)/([^/]+)/`)
	classicELB        = regexp.MustCompile(`^ELB ([^/]+)$`)
	vpcEndpoint       = regexp.MustCompile(`^VPC Endpoint Interface (vpce-[0-9a-f]+)$`)
	lambdaDescription = regexp.MustCompile(`^AWS Lambda VPC ENI-(.+?)-[0-9a-f-]{36}$`)
	// Shapes AWS writes for infrastructure that carries no tags. Anchored at
	// both ends so a person who happened to start a note with "Amazon EKS" is
	// not parsed as a cluster (SEC-23): a description that does not match the
	// whole shape is not a description we recognise.
	efsMountTarget        = regexp.MustCompile(`^EFS mount target for fs-[0-9a-f]+ \(fsmt-[0-9a-f]+\)$`)
	eksCluster            = regexp.MustCompile(`^Amazon EKS ([A-Za-z0-9][A-Za-z0-9_-]{0,99})$`)
	eksPod                = regexp.MustCompile(`^aws-K8S-i-[0-9a-f]+$`)
	natGatewayDescription = regexp.MustCompile(`^Interface for NAT Gateway nat-[0-9a-f]+$`)
)

// nameOf turns an ENI into something worth showing, or returns "".
//
// The Name tag is preferred over the description because it is what the
// customer calls the thing. Everything after it is a known AWS shape, parsed
// rather than forwarded: a description field holds free text, and free text
// from a customer account is not something to ship without looking at it.
func nameOf(iface ec2types.NetworkInterface) (name, kind string) {
	for _, tag := range iface.TagSet {
		if tag.Key == nil || *tag.Key != "Name" || tag.Value == nil {
			continue
		}
		if name := strings.TrimSpace(*tag.Value); name != "" && !isResourceID(name) {
			return name, "tag"
		}
	}

	description := ""
	if iface.Description != nil {
		description = strings.TrimSpace(*iface.Description)
	}
	switch {
	case elbDescription.MatchString(description):
		return elbDescription.FindStringSubmatch(description)[1], "load-balancer"
	case classicELB.MatchString(description):
		return classicELB.FindStringSubmatch(description)[1], "load-balancer"
	case vpcEndpoint.MatchString(description):
		return vpcEndpoint.FindStringSubmatch(description)[1], "vpc-endpoint"
	case lambdaDescription.MatchString(description):
		return lambdaDescription.FindStringSubmatch(description)[1], "lambda"
	case strings.HasPrefix(description, "RDSNetworkInterface"):
		return "rds", "rds"
	case strings.HasPrefix(description, "ElastiCache "):
		return "elasticache", "elasticache"
	case strings.HasPrefix(description, "RedshiftNetworkInterface"):
		return "redshift", "redshift"
	case strings.HasPrefix(description, "DMSNetworkInterface"):
		return "dms", "dms"
	case efsMountTarget.MatchString(description):
		// The filesystem id is not carried. `fs-0a1b2c3d` is not a name
		// anybody recognises, and an approver deciding about shared storage is
		// deciding about shared storage.
		return "efs", "efs"
	case eksCluster.MatchString(description):
		return eksCluster.FindStringSubmatch(description)[1], "eks"
	case eksPod.MatchString(description):
		// The EKS CNI plugin names these after the node's instance id, which
		// is the level EKS attribution already stops at. Saying so is better
		// than an address and honest about the limit.
		return "eks-node", "eks"
	case natGatewayDescription.MatchString(description):
		return "nat-gateway", "nat-gateway"
	}

	if name, kind := byInterfaceType(iface.InterfaceType); name != "" {
		return name, kind
	}

	if name := bySecurityGroup(iface.Groups); name != "" {
		return name, "security-group"
	}

	// An unrecognised description is deliberately not forwarded. The control
	// plane shows the address, which is honest, rather than free text that
	// might be a note to a colleague.
	return "", ""
}

// byInterfaceType names the managed interfaces AWS labels for us.
//
// The strongest signal available and the one that was being thrown away.
// InterfaceType is a closed enum AWS sets itself: it is not customer text, it
// cannot carry a note to a colleague, and it is present on exactly the
// interfaces nobody tags — a NAT gateway, a transit gateway, a network load
// balancer. Every account has those and none of them has ever had a Name tag.
//
// What each one buys an operator is a fact about the traffic rather than a
// service name. "This workload reaches the NAT gateway" is not the name of a
// service, but it answers the question the scope is asked: an approver seeing
// `nat-gateway` knows the traffic left the VPC for the internet, which is a
// decision they can make. `10.0.0.9` is not.
//
// Deliberately not listed: `interface`, `branch` and `trunk` say only that
// something made an ENI in the ordinary way, and `efa` is a high-performance
// fabric that says nothing about what runs on it. Naming those would put a
// word in front of an approver that carries no information, which is worse
// than the address it replaced.
func byInterfaceType(kind ec2types.NetworkInterfaceType) (name, label string) {
	switch kind {
	case ec2types.NetworkInterfaceTypeNatGateway:
		return "nat-gateway", "nat-gateway"
	case ec2types.NetworkInterfaceTypeTransitGateway:
		return "transit-gateway", "transit-gateway"
	case ec2types.NetworkInterfaceTypeNetworkLoadBalancer:
		return "network-load-balancer", "load-balancer"
	case ec2types.NetworkInterfaceTypeLoadBalancer,
		ec2types.NetworkInterfaceTypeGatewayLoadBalancer,
		ec2types.NetworkInterfaceTypeGatewayLoadBalancerEndpoint:
		return "load-balancer", "load-balancer"
	case ec2types.NetworkInterfaceTypeApiGatewayManaged:
		return "api-gateway", "api-gateway"
	case ec2types.NetworkInterfaceTypeVpcEndpoint:
		return "vpc-endpoint", "vpc-endpoint"
	case ec2types.NetworkInterfaceTypeLambda:
		return "lambda", "lambda"
	case ec2types.NetworkInterfaceTypeGlobalAcceleratorManaged:
		return "global-accelerator", "global-accelerator"
	case ec2types.NetworkInterfaceTypeQuicksight:
		return "quicksight", "quicksight"
	}
	return "", ""
}

// generatedGroupName matches the security group names nobody chose.
//
// `default` is what AWS calls the group every VPC gets. `launch-wizard-N` is
// what the console calls the group it makes when somebody clicks through.
// `eks-cluster-sg-...`, `k8s-elb-...` and CloudFormation's
// `stack-Resource-HASH` are all generated by a controller. None of them is a
// label a person chose for a service, which is the only reason to trust a
// group name at all.
var generatedGroupName = regexp.MustCompile(
	`^(default|launch-wizard-[0-9]+|eks-cluster-sg-.*|k8s-.*|.*-[A-Z0-9]{10,})$`)

// groupSuffix is the `-sg` people append because the console shows the name in
// a list of security groups. It is noise in a scope that is not about security
// groups.
var groupSuffix = regexp.MustCompile(`[-_](sg|secgroup|security-group)$`)

// bySecurityGroup names an interface after the one group somebody named.
//
// The last resort, and the one that needs an argument. SEC-23 forwards an
// ENI's Name tag because it is "the label a customer chose for the thing", and
// refuses descriptions because those are free text a person typed a note into.
// A security group name is the first of those and not the second: it is a name
// field on a networking object, chosen deliberately, in a charset AWS
// restricts.
//
// It matters because it is the one label the accounts with the worst tag
// hygiene still have. Nobody tags an ENI — the console does not even show the
// field during instance launch — and almost everybody names a security group,
// because the console makes them type one.
//
// Two rules keep it honest. Generated names are excluded, because `default`
// and `launch-wizard-3` are what AWS called it rather than what anybody called
// the service. And an interface in several non-generated groups is not named
// at all: two groups are two claims about what this is, and picking one would
// be choosing which of a customer's two answers to show an approver.
//
// The kind is reported as `security-group` so a reader can discount it. This
// is the weakest source in the list and the register should say so.
func bySecurityGroup(groups []ec2types.GroupIdentifier) string {
	var named []string
	for _, group := range groups {
		if group.GroupName == nil {
			continue
		}
		name := strings.TrimSpace(*group.GroupName)
		if name == "" || generatedGroupName.MatchString(name) {
			continue
		}
		named = append(named, groupSuffix.ReplaceAllString(name, ""))
	}
	if len(named) != 1 || named[0] == "" {
		return ""
	}
	return named[0]
}

// resourceID matches a name that is an identifier: `i-0a1b2c3d4e5f60718`,
// `eni-0a1b2c3d`, and the `tf-20260814093211004500000003` Terraform generates
// when a name_prefix is used without a name.
//
// A hyphen, a short lowercase prefix, and eight or more hex digits. A service
// nobody would name that way, and an identifier everybody's tooling does.
var resourceID = regexp.MustCompile(`^[a-z][a-z0-9]{0,15}-[0-9a-f]{8,}$`)

// isResourceID reports whether a Name tag is another way of writing the
// address it would replace.
//
// A Name tag is trusted because it is the label a customer chose for the thing
// (SEC-23). Tooling writes that field too, and what it writes is an id — which
// is honest, and exactly as useful to an approver as `10.0.12.1`. Rejecting it
// is not a claim that the customer was wrong; it is a refusal to spend the one
// readable column in the scope on a second copy of the address.
func isResourceID(name string) bool {
	return resourceID.MatchString(name)
}
