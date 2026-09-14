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
	"unicode"

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
		// Interfaces nothing on the interface itself could name, which are
		// attached to an instance. Resolved in one further call below.
		byInstance := map[string][]string{}
		for _, iface := range ifaces {
			name, kind := nameOf(iface)
			if name == "" {
				if id := attachedInstance(iface); id != "" {
					byInstance[id] = append(byInstance[id], privateAddresses(iface)...)
				}
				continue
			}
			for _, address := range privateAddresses(iface) {
				resolved[address] = wire.Destination{Address: address, Name: name, Kind: kind}
			}
		}

		for address, endpoint := range r.endpointServices(ctx, ifaces) {
			resolved[address] = wire.Destination{
				Address: address, Name: endpointName(endpoint),
				Kind: "vpc-endpoint", Service: endpoint.service,
			}
		}

		for id, name := range r.instanceNames(ctx, byInstance) {
			for _, address := range byInstance[id] {
				resolved[address] = wire.Destination{
					Address: address, Name: name, Kind: "instance",
				}
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

// MaxCached bounds the resolver's memory.
//
// The cache used to live for one collection, so its size was the size of one
// window. It lives for the life of the daemon now, and an account with churn —
// ECS tasks get a new address every deploy, Lambda ENIs come and go — adds
// entries for ever without this.
//
// Generous on purpose. An account reaching more than this many distinct
// internal addresses in six hours is one where the cache was not going to help
// anyway, and the eviction below is a sweep rather than an LRU: the entries
// worth keeping are the ones still inside their TTL, and ranking the rest
// would be bookkeeping to decide which of two answers we are about to re-ask
// for anyway.
const MaxCached = 50_000

func (r *DestinationResolver) remember(address string, d wire.Destination, found bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.cache == nil {
		r.cache = map[string]cached{}
	}
	if len(r.cache) >= MaxCached {
		r.evictExpired(time.Now())
	}
	r.cache[address] = cached{destination: d, at: time.Now(), found: found}
}

// evictExpired drops everything past its TTL, and everything if that was not
// enough. Called with the lock held.
//
// Dropping the whole cache when a sweep frees nothing is deliberate. The
// alternative is a resolver that has reached its limit with live entries and
// stops caching anything new for ever — which is the same as no cache, except
// silent and holding 50,000 entries.
func (r *DestinationResolver) evictExpired(now time.Time) {
	ttl := r.TTL
	if ttl <= 0 {
		ttl = DefaultTTL
	}
	for address, entry := range r.cache {
		if now.Sub(entry.at) > ttl {
			delete(r.cache, address)
		}
	}
	if len(r.cache) >= MaxCached {
		r.cache = map[string]cached{}
	}
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
		if name := renderable(*tag.Value); name != "" && !isResourceID(name) {
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

	if name, kind := byManagedTag(iface.TagSet); name != "" {
		return name, kind
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
		name := renderable(*group.GroupName)
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

// managedTags are tag keys written by an AWS service rather than by a person,
// whose value is the name of the thing the interface belongs to.
//
// `aws:` is a reserved prefix — a customer cannot create a tag in it, and AWS
// rejects the API call that tries. So these are not "a label a customer chose"
// in the SEC-23 sense; they are AWS's own metadata, in the same category as a
// description AWS wrote, and the value is the service or environment name the
// customer gave the resource. `cluster.k8s.amazonaws.com/name` is not reserved
// but is written by the AWS VPC CNI, not by whoever deployed the workload.
//
// Ordered, because an interface can carry several: the most specific thing it
// belongs to wins. A task in a service is that service; a task in a cluster
// with no service is only in a cluster, and naming it after the cluster would
// give every standalone task in the account the same name.
var managedTags = []struct {
	key  string
	kind string
}{
	{"aws:ecs:serviceName", "ecs"},
	{"elasticbeanstalk:environment-name", "beanstalk"},
	{"cluster.k8s.amazonaws.com/name", "eks"},
}

func byManagedTag(tags []ec2types.Tag) (name, kind string) {
	have := map[string]string{}
	for _, tag := range tags {
		if tag.Key != nil && tag.Value != nil {
			have[*tag.Key] = renderable(*tag.Value)
		}
	}
	for _, candidate := range managedTags {
		if value := have[candidate.key]; value != "" && !isResourceID(value) {
			return value, candidate.kind
		}
	}
	return "", ""
}

// attachedInstance is the instance an interface belongs to, or "".
//
// Only interfaces attached to an instance are worth a second call. A NAT
// gateway, a load balancer and a VPC endpoint all have attachments that name
// no instance, and they were already named by their type.
func attachedInstance(iface ec2types.NetworkInterface) string {
	if iface.Attachment == nil || iface.Attachment.InstanceId == nil {
		return ""
	}
	return strings.TrimSpace(*iface.Attachment.InstanceId)
}

// instanceNames asks what the instances behind these interfaces are called.
//
// The common case in a real account, and the one the estate fixture calls out:
// people tag instances, not interfaces. The console shows a Name field when
// launching an instance and does not show one for the interface it creates, so
// an account with careful tag hygiene still has ENIs named nothing at all.
//
// Two things make the cost acceptable. The call is DescribeInstances, which
// the collector already makes and is already granted for source attribution —
// this adds no permission. And it is only made for interfaces that nothing
// else could name, so an account with tidy ENIs never pays for it.
//
// A failure is not an error. The addresses fall back to being shown as
// addresses, which is what they would have been anyway, and a scan is not
// worth losing over a name.
func (r *DestinationResolver) instanceNames(
	ctx context.Context, byInstance map[string][]string,
) map[string]string {
	if len(byInstance) == 0 {
		return nil
	}
	ids := make([]string, 0, len(byInstance))
	for id := range byInstance {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	names := map[string]string{}
	var token *string
	for {
		out, err := r.API.DescribeInstances(ctx, &ec2.DescribeInstancesInput{
			InstanceIds: ids, NextToken: token,
		})
		if err != nil {
			return names
		}
		for _, reservation := range out.Reservations {
			for _, instance := range reservation.Instances {
				if instance.InstanceId == nil {
					continue
				}
				if name := instanceName(instance.Tags); name != "" {
					names[*instance.InstanceId] = name
				}
			}
		}
		if out.NextToken == nil || *out.NextToken == "" {
			return names
		}
		token = out.NextToken
	}
}

// instanceName applies the same rule to an instance's tags as to an
// interface's: the Name a person chose, and not an identifier their tooling
// wrote into the same field.
func instanceName(tags []ec2types.Tag) string {
	for _, tag := range tags {
		if tag.Key == nil || *tag.Key != "Name" || tag.Value == nil {
			continue
		}
		if name := renderable(*tag.Value); name != "" && !isResourceID(name) {
			return name
		}
	}
	return ""
}

// endpointServices asks which AWS service each interface endpoint is for.
//
// An interface VPC endpoint puts an ENI in the customer's own subnet, so
// traffic to it is traffic to a private address. The flow log's
// `pkt-dst-aws-service` annotation covers AWS's published address ranges and
// this is not one, so the record says nothing: on the wire it is an internal
// API, and an agent calling Bedrock through one is invisible.
//
// That is the same blindness a self-hosted gateway produces, with one
// difference. A self-hosted gateway is something only the customer knows
// about. A VPC endpoint for com.amazonaws.<region>.bedrock-runtime is
// something AWS knows about, and asking a customer to declare it is asking
// them for an answer we could have looked up.
//
// Filtered by the endpoint ids the interfaces named, rather than listing every
// endpoint in the region. Custos reads what its own traffic already pointed
// at, and a call that enumerates an account's endpoints is a different claim
// about what this role does (SEC-16).
// endpoint is what one DescribeVpcEndpoints answer says about an address.
type endpoint struct {
	// service is AWS's name for what the endpoint is for.
	service string
	// tag is the Name the customer put on the endpoint resource. Empty for
	// most of them, and the whole answer for the ones AWS cannot explain.
	tag string
}

func (r *DestinationResolver) endpointServices(
	ctx context.Context, ifaces []ec2types.NetworkInterface,
) map[string]endpoint {
	byEndpoint := map[string][]string{}
	for _, iface := range ifaces {
		id := endpointID(iface)
		if id == "" {
			continue
		}
		byEndpoint[id] = append(byEndpoint[id], privateAddresses(iface)...)
	}
	if len(byEndpoint) == 0 {
		return nil
	}
	ids := make([]string, 0, len(byEndpoint))
	for id := range byEndpoint {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	services := map[string]endpoint{}
	var token *string
	for {
		out, err := r.API.DescribeVpcEndpoints(ctx, &ec2.DescribeVpcEndpointsInput{
			VpcEndpointIds: ids, NextToken: token,
		})
		if err != nil {
			// The addresses keep whatever the description gave them, which is
			// the endpoint id. A scan is not worth losing over a name.
			return services
		}
		for _, e := range out.VpcEndpoints {
			if e.VpcEndpointId == nil || e.ServiceName == nil {
				continue
			}
			service := strings.TrimSpace(*e.ServiceName)
			if service == "" {
				continue
			}
			found := endpoint{service: service, tag: instanceName(e.Tags)}
			for _, address := range byEndpoint[*e.VpcEndpointId] {
				services[address] = found
			}
		}
		if out.NextToken == nil || *out.NextToken == "" {
			return services
		}
		token = out.NextToken
	}
}

// endpointID is the vpce- id an interface belongs to, or "".
func endpointID(iface ec2types.NetworkInterface) string {
	description := ""
	if iface.Description != nil {
		description = strings.TrimSpace(*iface.Description)
	}
	if m := vpcEndpoint.FindStringSubmatch(description); m != nil {
		return m[1]
	}
	return ""
}

// endpointName is what an operator should see for an interface endpoint.
//
// The customer's own Name tag first, for the same reason it comes first
// everywhere else in this file: it is the word the account already uses for
// the thing, and no lookup of ours beats it (SEC-23). It comes back on the
// call we already make, and was being discarded.
//
// It matters most where AWS says least. `com.amazonaws.vpce.<region>.
// vpce-svc-0a1b2c3d` names a service somebody else published and AWS will not
// say what is behind it, so a scope entry reading `vpce-svc-0a1b2c3d` is an
// approval the operator has to guess at — while the customer's own Terraform
// very often called it what it is.
//
// The service name still travels either way. This changes what is printed,
// not what the destination is, so an endpoint a customer named `llm` is still
// classified from `com.amazonaws.us-east-1.bedrock-runtime`.
func endpointName(e endpoint) string {
	if e.tag != "" {
		return e.tag
	}
	return serviceShortName(e.service)
}

// serviceShortName is the part of an endpoint service name an operator reads.
//
// `com.amazonaws.us-east-1.bedrock-runtime` becomes `bedrock-runtime`. The
// region is already the scan's region and the prefix is the same on every one
// of them; what distinguishes one endpoint from another is the last segment,
// and it is the only part that fits in a scope.
//
// A private-link service somebody else published — `com.amazonaws.vpce.
// us-east-1.vpce-svc-0a1b2c3d` — has no such segment, so the id is kept. That
// is honest: we do not know what it is either.
func serviceShortName(service string) string {
	if strings.HasPrefix(service, "com.amazonaws.vpce.") {
		return service[strings.LastIndex(service, ".")+1:]
	}
	if after, ok := strings.CutPrefix(service, "com.amazonaws."); ok {
		if _, name, found := strings.Cut(after, "."); found && name != "" {
			return name
		}
	}
	return service
}

// MaxNameLength bounds a destination name, in characters.
//
// An AWS tag value may be 256 characters. A scope line is read by a person
// deciding whether to grant authority, and a 256-character label in it pushes
// the address — the part that identifies the host — off the end of the line.
// Sixty-four is longer than any service name anybody uses and short enough to
// sit beside an address.
//
// Characters and not bytes. A tag value is UTF-8: `請求API-サービス` is eleven
// characters and thirty-one bytes, and a byte bound would cut a name that fits
// — or, worse, cut it mid-character and ship a broken sequence into the report.
const MaxNameLength = 64

// renderable turns a customer-authored label into something that can be put in
// a line of a report.
//
// Names come from tag values and security group names, which are text a person
// typed. Nothing here is about safety in the injection sense — the report
// escapes what it renders and the wire types cannot carry a payload (SEC-18) —
// it is about a label arriving in a column-aligned terminal table with a tab in
// it, or a newline, and breaking the one artefact an operator reads before
// granting authority.
//
// Whitespace of any kind collapses to a single space, because a name that came
// from a copy-paste is still that customer's name for the thing. Other control
// characters are dropped: there is no reading of a name in which a bell
// character is part of it. Over the cap, the name is cut and marked, because
// the address beside it is what identifies the host and a half-name with an
// ellipsis is more use than no name.
func renderable(value string) string {
	var b strings.Builder
	space := false
	for _, r := range value {
		switch {
		case unicode.IsSpace(r):
			space = b.Len() > 0
		case unicode.IsControl(r):
			// Dropped entirely, and does not count as a separator.
		default:
			if space {
				b.WriteRune(' ')
				space = false
			}
			b.WriteRune(r)
		}
	}
	runes := []rune(b.String())
	if len(runes) > MaxNameLength {
		return strings.TrimSpace(string(runes[:MaxNameLength])) + "\u2026"
	}
	return string(runes)
}
