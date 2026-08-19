package ingest

import (
	"context"
	"fmt"
	"regexp"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// Resolving a network interface to the principal behind it is the least
// glamorous problem in the product and the one that decides whether a finding
// is actionable or noise. A flow record names an ENI. A person needs a team.
//
// AWS makes this harder than it should be, and differently hard per compute
// type:
//
//	EC2     ENI attachment names an instance; the instance names an instance
//	        profile; the profile names a role. Fully resolvable.
//	Lambda  The ENI description embeds the function name and nothing else. The
//	        execution role needs a Lambda API call.
//	ECS     The ENI description is an attachment ARN. The task, task definition,
//	        and finally the task role are three more calls behind it.
//	EKS     The ENI belongs to the node, not the pod. Node-level attribution is
//	        the honest ceiling without eBPF or audit logs.
//
// This resolver implements the EC2 path fully and the others to the point where
// a human can act — compute type plus a name they will recognise. Where the
// principal cannot be resolved the interface is reported as unattributed rather
// than guessed at (SEC-20), because a wrong owner is worse than no owner: it
// routes the finding to someone who correctly ignores it.

var (
	lambdaENI = regexp.MustCompile(`^AWS Lambda VPC ENI[- ](?P<fn>.+?)-[0-9a-f-]{36}$`)
	ecsENI    = regexp.MustCompile(`^arn:aws:ecs:[^:]+:\d+:attachment/`)
	eksENI    = regexp.MustCompile(`^aws-K8S-i-|^amazon-k8s-eni`)
)

// Attribution is what could be established about one interface.
type Attribution struct {
	wire.Attachment

	// Degraded records why a principal could not be resolved, in language that
	// belongs in a report rather than in a stack trace. Empty when resolved.
	Degraded string
}

// Resolver maps network interfaces to principals.
type Resolver struct {
	API       awsread.NetworkAPI
	AccountID string
}

// Resolve looks up the given interfaces and attributes each one.
func (r *Resolver) Resolve(ctx context.Context, interfaceIDs []string) ([]Attribution, error) {
	if len(interfaceIDs) == 0 {
		return nil, nil
	}

	var out []Attribution
	// DescribeNetworkInterfaces caps its filter list, so page through in
	// chunks. One request per interface would mean thousands of billable calls
	// on an account with thousands of ENIs.
	const chunk = 200
	for start := 0; start < len(interfaceIDs); start += chunk {
		end := min(start+chunk, len(interfaceIDs))

		ifaces, err := r.describe(ctx, interfaceIDs[start:end])
		if err != nil {
			return out, err
		}

		instanceIDs := make([]string, 0, len(ifaces))
		for _, iface := range ifaces {
			if iface.Attachment != nil && iface.Attachment.InstanceId != nil {
				instanceIDs = append(instanceIDs, *iface.Attachment.InstanceId)
			}
		}

		roles, err := r.instanceRoles(ctx, instanceIDs)
		if err != nil {
			// A failed instance lookup degrades attribution. It does not
			// invalidate the flow data already collected.
			roles = map[string]string{}
		}

		for _, iface := range ifaces {
			out = append(out, r.attribute(iface, roles))
		}
	}
	return out, nil
}

func (r *Resolver) describe(ctx context.Context, ids []string) ([]ec2types.NetworkInterface, error) {
	var (
		all   []ec2types.NetworkInterface
		token *string
	)
	for {
		out, err := r.API.DescribeNetworkInterfaces(ctx, &ec2.DescribeNetworkInterfacesInput{
			NetworkInterfaceIds: ids,
			NextToken:           token,
		})
		if err != nil {
			return all, fmt.Errorf("describing network interfaces: %w", err)
		}
		all = append(all, out.NetworkInterfaces...)
		if out.NextToken == nil || *out.NextToken == "" {
			return all, nil
		}
		token = out.NextToken
	}
}

// instanceRoles maps instance ID to the role ARN in its instance profile.
func (r *Resolver) instanceRoles(ctx context.Context, ids []string) (map[string]string, error) {
	roles := map[string]string{}
	if len(ids) == 0 {
		return roles, nil
	}

	var token *string
	for {
		out, err := r.API.DescribeInstances(ctx, &ec2.DescribeInstancesInput{
			InstanceIds: ids,
			NextToken:   token,
		})
		if err != nil {
			return roles, fmt.Errorf("describing instances: %w", err)
		}
		for _, reservation := range out.Reservations {
			for _, instance := range reservation.Instances {
				if instance.InstanceId == nil || instance.IamInstanceProfile == nil {
					continue
				}
				if arn := aws.ToString(instance.IamInstanceProfile.Arn); arn != "" {
					roles[*instance.InstanceId] = profileToRoleARN(arn, r.AccountID)
				}
			}
		}
		if out.NextToken == nil || *out.NextToken == "" {
			return roles, nil
		}
		token = out.NextToken
	}
}

// profileToRoleARN converts an instance profile ARN to the conventional role ARN.
//
// The two share a name by convention, not by guarantee. When they differ this
// produces a role ARN that does not exist, the identity reader fails to look it
// up, and the interface surfaces as unattributed — which is the correct way to
// be wrong here.
func profileToRoleARN(profileARN, accountID string) string {
	const marker = ":instance-profile/"
	idx := strings.Index(profileARN, marker)
	if idx < 0 {
		return ""
	}
	name := profileARN[idx+len(marker):]
	if accountID == "" {
		accountID = arnAccount(profileARN)
	}
	return fmt.Sprintf("arn:aws:iam::%s:role/%s", accountID, name)
}

func arnAccount(arn string) string {
	if parts := strings.Split(arn, ":"); len(parts) > 4 {
		return parts[4]
	}
	return ""
}

func (r *Resolver) attribute(iface ec2types.NetworkInterface, roles map[string]string) Attribution {
	a := Attribution{Attachment: wire.Attachment{
		InterfaceID: aws.ToString(iface.NetworkInterfaceId),
		Address:     aws.ToString(iface.PrivateIpAddress),
		SubnetID:    aws.ToString(iface.SubnetId),
	}}
	description := aws.ToString(iface.Description)

	switch {
	case iface.Attachment != nil && iface.Attachment.InstanceId != nil:
		a.Compute = "EC2"
		if role := roles[*iface.Attachment.InstanceId]; role != "" {
			a.Principal = role
		} else {
			a.Degraded = "EC2 instance has no instance profile attached"
		}

	case lambdaENI.MatchString(description):
		a.Compute = "Lambda"
		a.Degraded = fmt.Sprintf(
			"Lambda function %q — execution role needs a Lambda API call not yet wired up",
			lambdaENI.FindStringSubmatch(description)[1])

	case ecsENI.MatchString(description):
		a.Compute = "ECS"
		a.Degraded = "ECS task — task role is three API calls behind the attachment ARN"

	case eksENI.MatchString(description):
		a.Compute = "EKS"
		a.Degraded = "EKS node interface — pod-level attribution needs eBPF or audit logs"

	case string(iface.InterfaceType) != "" && string(iface.InterfaceType) != "interface":
		// NAT gateways, VPC endpoints, load balancers. Not workloads at all.
		a.Compute = string(iface.InterfaceType)
		a.Degraded = "not a workload interface"

	default:
		a.Degraded = "interface type not recognised"
	}

	// Tags are the customer's own metadata and beat every inference above.
	for _, tag := range iface.TagSet {
		if strings.EqualFold(aws.ToString(tag.Key), "custos:principal") {
			if value := aws.ToString(tag.Value); value != "" {
				a.Principal = value
				a.Degraded = ""
			}
		}
	}

	return a
}

// Attachments splits attributions into resolved and degraded. SEC-20: they are
// reported separately and never merged.
func Attachments(attributions []Attribution) (resolved []wire.Attachment, degraded []Attribution) {
	for _, a := range attributions {
		if a.Principal != "" {
			resolved = append(resolved, a.Attachment)
		} else {
			degraded = append(degraded, a)
		}
	}
	return resolved, degraded
}
