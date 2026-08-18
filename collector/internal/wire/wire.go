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
	AccountID     string    `json:"account_id"`
	InterfaceID   string    `json:"interface_id"`
	SrcAddr       string    `json:"srcaddr"`
	DstAddr       string    `json:"dstaddr"`
	SrcPort       int       `json:"srcport"`
	DstPort       int       `json:"dstport"`
	Protocol      int       `json:"protocol"`
	Packets       int64     `json:"packets"`
	Bytes         int64     `json:"bytes"`
	Start         time.Time `json:"start"`
	End           time.Time `json:"end"`
	Action        string    `json:"action"`
	LogStatus     string    `json:"log_status"`
	VpcID         string    `json:"vpc_id"`
	SubnetID      string    `json:"subnet_id"`
	Direction     Direction `json:"direction"`
	DstAWSService string    `json:"dst_aws_service"`
	TCPFlags      int       `json:"tcp_flags"`
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

// Batch is the unit of shipment. This is the complete set of things that ever
// leaves a customer account.
type Batch struct {
	AccountID   string           `json:"account_id"`
	Region      string           `json:"region"`
	WindowStart time.Time        `json:"window_start"`
	WindowEnd   time.Time        `json:"window_end"`
	Collector   string           `json:"collector_version"`
	Flows       []FlowRecord     `json:"flows"`
	Requests    []InboundRequest `json:"requests"`
	Principals  []PrincipalFacts `json:"principals"`
	Attachments []Attachment     `json:"attachments"`
}
