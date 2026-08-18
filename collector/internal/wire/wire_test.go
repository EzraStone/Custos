package wire

import (
	"reflect"
	"strings"
	"testing"
)

// allowedFields is the complete set of fields permitted on the wire.
//
// Adding a field to any wire type requires adding it here first. That is the
// point: carrying prompt or completion text out of a customer account cannot
// happen by accident, only by deliberately editing a test named after the
// invariant it breaks.
var allowedFields = map[string]map[string]bool{
	"FlowRecord": {
		"AccountID": true, "InterfaceID": true, "SrcAddr": true, "DstAddr": true,
		"SrcPort": true, "DstPort": true, "Protocol": true, "Packets": true,
		"Bytes": true, "Start": true, "End": true, "Action": true,
		"LogStatus": true, "VpcID": true, "SubnetID": true, "Direction": true,
		"DstAWSService": true, "TCPFlags": true,
	},
	"InboundRequest": {
		"At": true, "Target": true, "SentBytes": true, "ReceivedBytes": true,
	},
	"PrincipalFacts": {
		"Principal": true, "AccountID": true, "IAMPath": true, "Compute": true,
		"RoleTags": true, "ResourceTags": true, "Actions": true, "AssumableRole": true,
	},
	"Attachment": {
		"InterfaceID": true, "Principal": true, "Address": true,
		"SubnetID": true, "Compute": true,
	},
	"Batch": {
		"AccountID": true, "Region": true, "WindowStart": true, "WindowEnd": true,
		"Collector": true, "Flows": true, "Requests": true, "Principals": true,
		"Attachments": true,
	},
}

// TestWireTypesCarryNoPayload enforces SEC-18 by reflection.
func TestWireTypesCarryNoPayload(t *testing.T) {
	types := []any{FlowRecord{}, InboundRequest{}, PrincipalFacts{}, Attachment{}, Batch{}}

	for _, v := range types {
		rt := reflect.TypeOf(v)
		allowed, ok := allowedFields[rt.Name()]
		if !ok {
			t.Fatalf("wire type %s has no field allowlist; add one before shipping it", rt.Name())
		}
		for i := 0; i < rt.NumField(); i++ {
			name := rt.Field(i).Name
			if !allowed[name] {
				t.Errorf("SEC-18: %s.%s is not in the wire allowlist", rt.Name(), name)
			}
		}
		if got, want := rt.NumField(), len(allowed); got != want {
			t.Errorf("%s has %d fields but %d are allowlisted", rt.Name(), got, want)
		}
	}
}

// TestNoPayloadShapedFieldNames is a second, cruder guard. Reflection catches a
// field that was added; this catches one that was added and allowlisted without
// anyone thinking about what it means.
func TestNoPayloadShapedFieldNames(t *testing.T) {
	suspicious := []string{
		"body", "payload", "prompt", "completion", "content", "message",
		"text", "request_body", "response_body", "query", "input", "output",
	}
	for typeName, fields := range allowedFields {
		for field := range fields {
			lower := strings.ToLower(field)
			for _, bad := range suspicious {
				if lower == bad {
					t.Errorf("SEC-18: %s.%s is payload-shaped and must not be on the wire",
						typeName, field)
				}
			}
		}
	}
}
