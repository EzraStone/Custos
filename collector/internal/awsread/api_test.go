package awsread

import (
	"reflect"
	"testing"
)

// TestEveryAWSMethodIsReadOnly closes the loop between the two SEC-16 layers.
//
// The interfaces in api.go make writes un-callable. The verb allowlist in
// awsread.go describes what read-only means. This asserts they agree: every
// method the collector can reach passes the same check the audit layer applies.
//
// It catches the realistic mistake — someone adds a method to an interface
// because they need it, and it happens to mutate.
func TestEveryAWSMethodIsReadOnly(t *testing.T) {
	surfaces := map[string]reflect.Type{
		"LogsAPI":       reflect.TypeOf((*LogsAPI)(nil)).Elem(),
		"ObjectAPI":     reflect.TypeOf((*ObjectAPI)(nil)).Elem(),
		"NetworkAPI":    reflect.TypeOf((*NetworkAPI)(nil)).Elem(),
		"IdentityAPI":   reflect.TypeOf((*IdentityAPI)(nil)).Elem(),
		"ServerlessAPI": reflect.TypeOf((*ServerlessAPI)(nil)).Elem(),
	}

	total := 0
	for name, iface := range surfaces {
		if iface.NumMethod() == 0 {
			t.Errorf("%s declares no methods", name)
		}
		for i := 0; i < iface.NumMethod(); i++ {
			method := iface.Method(i).Name
			total++
			if !IsReadOnly(method) {
				t.Errorf("SEC-16: %s.%s is reachable but is not a read-only operation",
					name, method)
			}
		}
	}

	// A tripwire on surface growth. The collector needs a small, readable set
	// of AWS calls; if this number climbs, someone should be asked why.
	if total > 20 {
		t.Errorf("AWS surface has grown to %d methods; keep it small enough to audit", total)
	}
}

// TestStartQueryIsNotReachable names the specific temptation.
func TestStartQueryIsNotReachable(t *testing.T) {
	iface := reflect.TypeOf((*LogsAPI)(nil)).Elem()
	for i := 0; i < iface.NumMethod(); i++ {
		if iface.Method(i).Name == "StartQuery" {
			t.Fatal("SEC-16: StartQuery creates a billable resource in the customer's account")
		}
	}
}
