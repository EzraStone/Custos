package ingest

import (
	"context"
	"errors"
	"net/url"
	"slices"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/iam"
	iamtypes "github.com/aws/aws-sdk-go-v2/service/iam/types"
)

type fakeIAM struct {
	path     string
	tags     map[string]string
	attached map[string]string // policy arn -> document (raw json)
	inline   map[string]string // policy name -> document (raw json)
	getRole  error
	encode   bool // URL-encode documents the way IAM actually does
}

func (f *fakeIAM) doc(raw string) *string {
	if f.encode {
		return aws.String(url.QueryEscape(raw))
	}
	return aws.String(raw)
}

func (f *fakeIAM) GetRole(_ context.Context, in *iam.GetRoleInput,
	_ ...func(*iam.Options)) (*iam.GetRoleOutput, error) {
	if f.getRole != nil {
		return nil, f.getRole
	}
	var tags []iamtypes.Tag
	for k, v := range f.tags {
		tags = append(tags, iamtypes.Tag{Key: aws.String(k), Value: aws.String(v)})
	}
	return &iam.GetRoleOutput{Role: &iamtypes.Role{
		RoleName: in.RoleName, Path: aws.String(f.path), Tags: tags,
	}}, nil
}

func (f *fakeIAM) ListRoleTags(context.Context, *iam.ListRoleTagsInput,
	...func(*iam.Options)) (*iam.ListRoleTagsOutput, error) {
	return &iam.ListRoleTagsOutput{}, nil
}

func (f *fakeIAM) ListAttachedRolePolicies(context.Context, *iam.ListAttachedRolePoliciesInput,
	...func(*iam.Options)) (*iam.ListAttachedRolePoliciesOutput, error) {
	out := &iam.ListAttachedRolePoliciesOutput{}
	for arn := range f.attached {
		out.AttachedPolicies = append(out.AttachedPolicies,
			iamtypes.AttachedPolicy{PolicyArn: aws.String(arn)})
	}
	return out, nil
}

func (f *fakeIAM) ListRolePolicies(context.Context, *iam.ListRolePoliciesInput,
	...func(*iam.Options)) (*iam.ListRolePoliciesOutput, error) {
	out := &iam.ListRolePoliciesOutput{}
	for name := range f.inline {
		out.PolicyNames = append(out.PolicyNames, name)
	}
	return out, nil
}

func (f *fakeIAM) GetRolePolicy(_ context.Context, in *iam.GetRolePolicyInput,
	_ ...func(*iam.Options)) (*iam.GetRolePolicyOutput, error) {
	raw, ok := f.inline[aws.ToString(in.PolicyName)]
	if !ok {
		return nil, errors.New("no such policy")
	}
	return &iam.GetRolePolicyOutput{PolicyDocument: f.doc(raw)}, nil
}

func (f *fakeIAM) GetPolicy(_ context.Context, in *iam.GetPolicyInput,
	_ ...func(*iam.Options)) (*iam.GetPolicyOutput, error) {
	if _, ok := f.attached[aws.ToString(in.PolicyArn)]; !ok {
		return nil, errors.New("no such policy")
	}
	return &iam.GetPolicyOutput{Policy: &iamtypes.Policy{
		Arn: in.PolicyArn, DefaultVersionId: aws.String("v1"),
	}}, nil
}

func (f *fakeIAM) GetPolicyVersion(_ context.Context, in *iam.GetPolicyVersionInput,
	_ ...func(*iam.Options)) (*iam.GetPolicyVersionOutput, error) {
	raw, ok := f.attached[aws.ToString(in.PolicyArn)]
	if !ok {
		return nil, errors.New("no such policy")
	}
	return &iam.GetPolicyVersionOutput{PolicyVersion: &iamtypes.PolicyVersion{
		Document: f.doc(raw),
	}}, nil
}

const roleARN = "arn:aws:iam::447120043318:role/finance/service-role/finance-close"

func read(t *testing.T, api *fakeIAM) []string {
	t.Helper()
	facts, err := (&IdentityReader{API: api}).Read(context.Background(), roleARN)
	if err != nil {
		t.Fatal(err)
	}
	return facts.Actions
}

// TestPolicyDocumentsAreURLDecoded guards the most dangerous possible bug.
//
// IAM returns policy documents URL-encoded. Forgetting that makes every policy
// fail to parse, so every role reports no permissions, so every finding says
// "read only" — a report that looks like good news and is entirely false.
func TestPolicyDocumentsAreURLDecoded(t *testing.T) {
	api := &fakeIAM{encode: true, attached: map[string]string{
		"arn:aws:iam::aws:policy/Billing": `{"Statement":[
			{"Effect":"Allow","Action":["dynamodb:UpdateItem","s3:GetObject"]}]}`,
	}}
	got := read(t, api)
	if !slices.Contains(got, "dynamodb:UpdateItem") {
		t.Fatalf("URL-encoded policy not decoded: %v", got)
	}
}

func TestInlineAndAttachedPoliciesAreBothRead(t *testing.T) {
	api := &fakeIAM{
		encode:   true,
		attached: map[string]string{"arn:p1": `{"Statement":[{"Effect":"Allow","Action":"s3:GetObject"}]}`},
		inline:   map[string]string{"inline1": `{"Statement":[{"Effect":"Allow","Action":"rds:ModifyDBInstance"}]}`},
	}
	got := read(t, api)
	for _, want := range []string{"s3:GetObject", "rds:ModifyDBInstance"} {
		if !slices.Contains(got, want) {
			t.Errorf("missing %q from %v", want, got)
		}
	}
}

// TestActionAcceptsStringOrArray: both are valid IAM and both appear in real
// policies.
func TestActionAcceptsStringOrArray(t *testing.T) {
	api := &fakeIAM{encode: true, inline: map[string]string{
		"single": `{"Statement":[{"Effect":"Allow","Action":"s3:PutObject"}]}`,
		"many":   `{"Statement":[{"Effect":"Allow","Action":["ecs:UpdateService","ecs:RunTask"]}]}`,
	}}
	got := read(t, api)
	for _, want := range []string{"s3:PutObject", "ecs:UpdateService", "ecs:RunTask"} {
		if !slices.Contains(got, want) {
			t.Errorf("missing %q from %v", want, got)
		}
	}
}

func TestDenyStatementsAreNotCountedAsCapability(t *testing.T) {
	api := &fakeIAM{encode: true, inline: map[string]string{
		"p": `{"Statement":[{"Effect":"Deny","Action":"s3:DeleteBucket"}]}`,
	}}
	if got := read(t, api); slices.Contains(got, "s3:DeleteBucket") {
		t.Fatalf("a Deny must not be reported as granted: %v", got)
	}
}

// TestWildcardsAreRecordedVerbatim: expanding s3:* into a guessed action list
// would invent findings. The control plane treats the wildcard as destructive.
func TestWildcardsAreRecordedVerbatim(t *testing.T) {
	api := &fakeIAM{encode: true, inline: map[string]string{
		"p": `{"Statement":[{"Effect":"Allow","Action":"s3:*"}]}`,
	}}
	got := read(t, api)
	if !slices.Contains(got, "s3:*") {
		t.Fatalf("wildcard lost: %v", got)
	}
	if len(got) != 1 {
		t.Fatalf("wildcard must not be expanded: %v", got)
	}
}

// TestUnparseablePolicyIsFlaggedNotSkipped: silently dropping a policy we
// cannot read produces an understated finding, which is the wrong direction.
func TestUnparseablePolicyIsFlaggedNotSkipped(t *testing.T) {
	api := &fakeIAM{encode: true, inline: map[string]string{"p": `{ not json`}}
	got := read(t, api)
	if !slices.Contains(got, "custos:unparsed-policy") {
		t.Fatalf("unparseable policy not flagged: %v", got)
	}
}

func TestAssumeRoleIsRecordedAsLateralMovement(t *testing.T) {
	api := &fakeIAM{encode: true, inline: map[string]string{
		"p": `{"Statement":[{"Effect":"Allow","Action":"sts:AssumeRole"}]}`,
	}}
	facts, err := (&IdentityReader{API: api}).Read(context.Background(), roleARN)
	if err != nil {
		t.Fatal(err)
	}
	if len(facts.AssumableRole) == 0 {
		t.Fatal("sts:AssumeRole must be recorded as assumable reach")
	}
}

func TestRolePathIsCapturedForAttribution(t *testing.T) {
	api := &fakeIAM{encode: true, path: "/finance/service-role/"}
	facts, err := (&IdentityReader{API: api}).Read(context.Background(), roleARN)
	if err != nil {
		t.Fatal(err)
	}
	if facts.IAMPath != "/finance/service-role/" {
		t.Fatalf("IAM path lost: %q", facts.IAMPath)
	}
}

func TestRoleTagsAreCaptured(t *testing.T) {
	api := &fakeIAM{encode: true, tags: map[string]string{"team": "finance-platform"}}
	facts, err := (&IdentityReader{API: api}).Read(context.Background(), roleARN)
	if err != nil {
		t.Fatal(err)
	}
	if facts.RoleTags["team"] != "finance-platform" {
		t.Fatalf("tags lost: %v", facts.RoleTags)
	}
}

func TestRoleNameIsExtractedThroughAnIAMPath(t *testing.T) {
	if got := roleNameFromARN(roleARN); got != "finance-close" {
		t.Fatalf("got %q", got)
	}
	if got := roleNameFromARN("arn:aws:iam::1:role/plain"); got != "plain" {
		t.Fatalf("got %q", got)
	}
	if got := roleNameFromARN("arn:aws:iam::1:user/somebody"); got != "" {
		t.Fatalf("a user ARN is not a role: %q", got)
	}
}

func TestActionsAreDeterministicallyOrdered(t *testing.T) {
	api := &fakeIAM{encode: true, inline: map[string]string{
		"p": `{"Statement":[{"Effect":"Allow","Action":["s3:PutObject","ec2:DescribeTags","iam:GetRole"]}]}`,
	}}
	first, second := read(t, api), read(t, api)
	if !slices.Equal(first, second) || !slices.IsSorted(first) {
		t.Fatalf("actions must be sorted and stable: %v vs %v", first, second)
	}
}

func TestMissingRoleIsAnError(t *testing.T) {
	api := &fakeIAM{getRole: errors.New("NoSuchEntity")}
	if _, err := (&IdentityReader{API: api}).Read(context.Background(), roleARN); err == nil {
		t.Fatal("a missing role must be reported")
	}
}
