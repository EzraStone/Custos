package awsread

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"regexp"
	"strings"
	"testing"
)

// TestEveryOperationIsGrantedByTheTerraform guards a silent onboarding failure.
//
// The interfaces in api.go are the complete set of AWS operations the collector
// can perform. The Terraform module in deploy/terraform is what a customer
// applies to permit them. Nothing has connected the two, and a call the policy
// does not grant fails with AccessDenied on the first scan of an account the
// customer has just spent a change request onboarding.
//
// It found one when it was written: the collector reads flow logs and load
// balancer access logs from S3 with ListObjectsV2 and GetObject, and the policy
// granted neither. S3 is the cheaper flow log destination and the only place
// access logs go, so this was the path the target profile was most likely to
// take.
func TestEveryOperationIsGrantedByTheTerraform(t *testing.T) {
	granted := grantedActions(t)

	for _, op := range declaredOperations(t) {
		action, ok := iamAction(op)
		if !ok {
			t.Fatalf("%s has no IAM action mapped; add it to iamActions", op)
		}
		if !granted[action] {
			t.Errorf(
				"%s is called by the collector and %s is not in the Terraform "+
					"policy: the first scan of a customer's account fails with "+
					"AccessDenied", op, action)
		}
	}
}

// TestTheGrantIsNotWiderThanTheCode is the same check facing the other way.
//
// A granted action nothing calls is a permission a customer was asked for and
// did not need, which is exactly the thing a security review finds and asks
// about — and the honest answer, "we used to call that", is not one that helps.
func TestTheGrantIsNotWiderThanTheCode(t *testing.T) {
	called := map[string]bool{}
	for _, op := range declaredOperations(t) {
		if action, ok := iamAction(op); ok {
			called[action] = true
		}
	}

	for action := range grantedActions(t) {
		if !called[action] && !justified[action] {
			t.Errorf("%s is granted and never called; remove it or justify it "+
				"in the justified map with the reason", action)
		}
	}
}

// justified names actions the policy grants that no interface method calls,
// with why. Every entry is a permission a customer is being asked for, so each
// one has to survive being read aloud in a security review.
var justified = map[string]bool{
	// Preflight asks whether flow logs exist and where they go, before any
	// scan. Without it the answer to "why did the scan find nothing" is a
	// guess.
	"ec2:DescribeFlowLogs": true,
	// Destination naming reads subnet and VPC names so a register entry can
	// say "billing-api" rather than "10.0.4.23".
	"ec2:DescribeSubnets": true,
	"ec2:DescribeVpcs":    true,
	"ec2:DescribeTags":    true,
	// Attribution reads resource tags from instances it has already described.
	"iam:ListRoles": true,
	// Compute attribution walks clusters and services to reach a task.
	"ecs:ListServices":     true,
	"ecs:DescribeServices": true,
	"ecs:ListTasks":        true,
	"lambda:ListFunctions": true,
	// EKS resolves node-level attribution for pods.
	"eks:ListClusters":    true,
	"eks:DescribeCluster": true,
	// Paged reads of a log group, used when a group is named rather than
	// searched.
	"logs:DescribeLogStreams": true,
	"logs:GetLogEvents":       true,
}

// iamActions maps an SDK operation to the IAM action that authorises it.
//
// Mostly the same name with a service prefix, and the exceptions are the point:
// ListObjectsV2 is authorised by s3:ListBucket, and a test that assumed the
// names matched would have declared the missing S3 grant fine.
var iamActions = map[string]string{
	"ListObjectsV2": "s3:ListBucket",
	"GetObject":     "s3:GetObject",
}

// service maps an operation to its service prefix, for the ordinary case.
var service = map[string]string{
	"FilterLogEvents": "logs", "DescribeLogGroups": "logs",
	"DescribeNetworkInterfaces": "ec2", "DescribeInstances": "ec2",
	"GetRole": "iam", "ListRoleTags": "iam", "ListAttachedRolePolicies": "iam",
	"ListRolePolicies": "iam", "GetRolePolicy": "iam", "GetPolicy": "iam",
	"GetPolicyVersion":         "iam",
	"GetFunctionConfiguration": "lambda",
	"DescribeTasks":            "ecs", "DescribeTaskDefinition": "ecs",
	"ListClusters": "ecs",
	"LookupEvents": "cloudtrail",
}

func iamAction(operation string) (string, bool) {
	if action, ok := iamActions[operation]; ok {
		return action, true
	}
	if prefix, ok := service[operation]; ok {
		return prefix + ":" + operation, true
	}
	return "", false
}

// declaredOperations reads api.go rather than a list, so that adding a method
// to an interface is enough to be checked.
func declaredOperations(t *testing.T) []string {
	t.Helper()

	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "api.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}

	var out []string
	ast.Inspect(file, func(n ast.Node) bool {
		iface, ok := n.(*ast.InterfaceType)
		if !ok {
			return true
		}
		for _, method := range iface.Methods.List {
			for _, name := range method.Names {
				out = append(out, name.Name)
			}
		}
		return true
	})
	if len(out) == 0 {
		t.Fatal("no interface methods found in api.go; the parse is wrong")
	}
	return out
}

var actionLine = regexp.MustCompile(`"([a-z0-9]+:[A-Za-z0-9*]+)"`)

// grantedActions reads the actions out of the read_only policy document only.
//
// Not the whole file. The assume document describes who may take the role
// rather than what it may do, and the deny document lists actions precisely
// because they are refused — reading either as a grant would make this test
// assert the opposite of what it means to.
func grantedActions(t *testing.T) map[string]bool {
	t.Helper()

	raw, err := os.ReadFile("../../deploy/terraform/iam.tf")
	if err != nil {
		t.Skipf("terraform module not present: %v", err)
	}

	text := string(raw)
	start := strings.Index(text, `data "aws_iam_policy_document" "read_only"`)
	if start < 0 {
		t.Fatal(`no read_only policy document in iam.tf`)
	}
	text = text[start:]
	if end := strings.Index(text, `data "aws_iam_policy_document" "deny_writes"`); end > 0 {
		text = text[:end]
	}

	granted := map[string]bool{}
	for _, m := range actionLine.FindAllStringSubmatch(text, -1) {
		if !strings.Contains(m[1], "*") {
			granted[m[1]] = true
		}
	}
	if len(granted) == 0 {
		t.Fatal("no actions parsed from the read_only document; the regexp is wrong")
	}
	return granted
}
