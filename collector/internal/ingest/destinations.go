package ingest

import (
	"context"
	"fmt"
	"net/netip"
	"regexp"
	"sort"
	"strings"

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
}

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

	var out []wire.Destination
	for start := 0; start < len(internal); start += describeChunk {
		end := min(start+describeChunk, len(internal))

		ifaces, err := r.describeByAddress(ctx, internal[start:end])
		if err != nil {
			// Naming is an improvement on the report, not a precondition for
			// it. A failure here must not cost the customer the scan.
			return out, err
		}
		for _, iface := range ifaces {
			for _, address := range privateAddresses(iface) {
				if name, kind := nameOf(iface); name != "" {
					out = append(out, wire.Destination{Address: address, Name: name, Kind: kind})
				}
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Address < out[j].Address })
	return out, nil
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
