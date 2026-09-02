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
)

// nameOf turns an ENI into something worth showing, or returns "".
//
// The Name tag is preferred over the description because it is what the
// customer calls the thing. Everything after it is a known AWS shape, parsed
// rather than forwarded: a description field holds free text, and free text
// from a customer account is not something to ship without looking at it.
func nameOf(iface ec2types.NetworkInterface) (name, kind string) {
	for _, tag := range iface.TagSet {
		if tag.Key != nil && *tag.Key == "Name" && tag.Value != nil && *tag.Value != "" {
			return strings.TrimSpace(*tag.Value), "tag"
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
	}

	// An unrecognised description is deliberately not forwarded. The control
	// plane shows the address, which is honest, rather than free text that
	// might be a note to a colleague.
	return "", ""
}
