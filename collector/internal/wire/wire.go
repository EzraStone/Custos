// Package wire defines the only types the collector is capable of sending.
//
// SEC-18 is enforced here, structurally. There is no field on any type in this
// package that can hold a prompt, a completion, or any other payload byte, and
// the shipper accepts nothing but these types. This is deliberately not a
// redaction step: a redaction step is a filter, filters have bugs and
// configuration, and a security reviewer is right not to trust one. An absent
// field has no bugs.
//
// A reviewer reading the collector for the first time should be able to
// establish the whole privacy claim by reading this file and confirming that
// ship.Send takes only these types.
package wire

import "time"

// Direction mirrors the VPC Flow Logs v5 flow-direction field.
type Direction string

const (
	Egress  Direction = "egress"
	Ingress Direction = "ingress"
)

// FlowRecord is one aggregated network flow. Byte counts and timings only.
type FlowRecord struct {
	AccountID string `json:"account_id"`

	// Region this record was read in.
	//
	// Not decoration, and not for the report. A private address is unique
	// within a region and nowhere else: 10.0.4.21 is the billing API in
	// us-east-1 and something entirely different in eu-west-1. A batch
	// covering both regions that keyed anything on the address alone would
	// name one of them wrongly, in the scope an operator reads before granting
	// authority.
	//
	// Empty on a single-region batch from an older collector, which is
	// unambiguous for the same reason.
	Region string `json:"region,omitempty"`

	InterfaceID string    `json:"interface_id"`
	SrcAddr     string    `json:"srcaddr"`
	DstAddr     string    `json:"dstaddr"`
	SrcPort     int       `json:"srcport"`
	DstPort     int       `json:"dstport"`
	Protocol    int       `json:"protocol"`
	Packets     int64     `json:"packets"`
	Bytes       int64     `json:"bytes"`
	Start       time.Time `json:"start"`
	End         time.Time `json:"end"`
	Action      string    `json:"action"`
	LogStatus   string    `json:"log_status"`
	VpcID       string    `json:"vpc_id"`
	SubnetID    string    `json:"subnet_id"`
	Direction   Direction `json:"direction"`

	// SrcAWSService and DstAWSService name the AWS service at each end, when
	// AWS recognises one. Both are needed because each describes its own end:
	// on a request to S3 the service is on the destination, and on the reply
	// it is on the source. Reading only the destination annotation means the
	// return leg of every AWS conversation arrives unattributed, and half of
	// what a workload reaches looks like traffic to an unknown address.
	SrcAWSService string `json:"src_aws_service"`
	DstAWSService string `json:"dst_aws_service"`

	TCPFlags int `json:"tcp_flags"`
}

// InboundRequest is one load balancer access log line, reduced to timing and
// size. The URL, the user agent, and the client address are all deliberately
// absent: correlation needs only when a request arrived and how big it was,
// and the rest would describe the people using the system rather than the
// software, which is the line the whole ethical position rests on.
type InboundRequest struct {
	At            time.Time `json:"at"`
	Target        string    `json:"target"`
	SentBytes     int64     `json:"sent_bytes"`
	ReceivedBytes int64     `json:"received_bytes"`
}

// PrincipalFacts is what IAM and resource describe calls revealed about a
// principal. Tags are included because attribution depends on them; tag values
// are customer-authored metadata about their own infrastructure.
type PrincipalFacts struct {
	Principal     string            `json:"principal"`
	AccountID     string            `json:"account_id"`
	IAMPath       string            `json:"iam_path"`
	Compute       string            `json:"compute"`
	RoleTags      map[string]string `json:"role_tags"`
	ResourceTags  map[string]string `json:"resource_tags"`
	Actions       []string          `json:"actions"`
	AssumableRole []string          `json:"assumable_roles"`
}

// Attachment maps a network interface to the principal running behind it.
type Attachment struct {
	InterfaceID string `json:"interface_id"`
	Principal   string `json:"principal"`
	Address     string `json:"address"`
	SubnetID    string `json:"subnet_id"`
	Compute     string `json:"compute"`
}

// Destination names an address a workload reached.
//
// The register's scope is what an operator reads before granting authority,
// and an address is not something anyone can make a decision about. Flow logs
// carry no hostname, but an ENI in the account does: AWS gives managed
// services structured descriptions, and the account's own workloads are
// already resolved to principals for attribution.
//
// Name is what to show. Kind says where it came from, so a reader can weigh a
// load balancer's own name against a guess from a port number.
//
// No new SEC-18 surface: this is the same category as Attachment, which
// already ships an address and the principal behind it. Free-text descriptions
// are parsed into a known shape rather than forwarded, so an operator who
// wrote something careless in an ENI description does not have it shipped
// verbatim.
type Destination struct {
	Address string `json:"address"`
	Name    string `json:"name"`
	Kind    string `json:"kind"`

	// Region the address was resolved in. Same reason as FlowRecord.Region: an
	// address names a different host in each region, and this is the field
	// that stops one region's name being shown against another's traffic.
	Region string `json:"region,omitempty"`
}

// Collection describes the collection itself rather than the account.
//
// It ships because the control plane cannot otherwise tell "this account is
// clean" from "this scan read a third of the traffic". Those produce identical
// findings and mean entirely different things, and the difference has to
// travel with the data rather than living in a log the customer never sees.
//
// Nothing here describes customer infrastructure, so it adds no SEC-18
// surface: they are counters about our own reading.
type Collection struct {
	LinesRead      int64 `json:"lines_read"`
	LinesParsed    int64 `json:"lines_parsed"`
	LinesMalformed int64 `json:"lines_malformed"`
	RecordsSkipped int64 `json:"records_skipped"`
	Truncated      bool  `json:"truncated"`
	HaveAccessLogs bool  `json:"have_access_logs"`

	// MissingFields names the flow log fields Custos uses that this account's
	// format does not carry.
	//
	// Drawn from a fixed vocabulary and never from the customer's own format
	// string (SEC-23): a format may legitimately contain field names we have
	// never heard of, and forwarding those would be forwarding customer text.
	//
	// It ships because it changes what the report may claim. An account whose
	// format has no port field has no MCP servers in its register, and a
	// report that does not say so is asserting an absence it never looked for.
	MissingFields []string `json:"missing_fields,omitempty"`

	// DirectionInferred and DirectionUndecided count records whose direction
	// the format did not carry. Undecided ones were dropped, so the second
	// number is coverage lost and belongs beside the parse counters.
	DirectionInferred  int64 `json:"direction_inferred,omitempty"`
	DirectionUndecided int64 `json:"direction_undecided,omitempty"`

	// ReadErrors counts AWS reads that failed after retries during this
	// collection.
	//
	// A count and not the messages: an AWS error string can quote a resource
	// ARN or a policy, and SEC-18 is the rule that nothing describing the
	// account's contents leaves it except through the named fields above. The
	// messages are printed locally, where the person who can act on them is.
	//
	// It ships because an interface nobody could describe produces a finding
	// with no owner, which is the same shape an untagged account produces. A
	// report that cannot tell those apart presents our throttling as a fact
	// about the customer's tagging.
	ReadErrors int64 `json:"read_errors,omitempty"`
}

// Batch is the unit of shipment. This is the complete set of things that ever
// leaves a customer account.
type Batch struct {
	AccountID    string           `json:"account_id"`
	Region       string           `json:"region"`
	WindowStart  time.Time        `json:"window_start"`
	WindowEnd    time.Time        `json:"window_end"`
	Collector    string           `json:"collector_version"`
	Collection   Collection       `json:"collection"`
	Flows        []FlowRecord     `json:"flows"`
	Requests     []InboundRequest `json:"requests"`
	Principals   []PrincipalFacts `json:"principals"`
	Attachments  []Attachment     `json:"attachments"`
	Destinations []Destination    `json:"destinations"`
}
