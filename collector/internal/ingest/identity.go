package ingest

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/iam"

	"github.com/EzraStone/Custos/collector/internal/awsread"
	"github.com/EzraStone/Custos/collector/internal/wire"
)

// Reading a role's policies is what turns "this agent talked to your billing
// API" into "this agent holds a credential that can write to your billing
// tables". The first is a curiosity. The second is a budget line.
//
// It is also the part most likely to be wrong in a way that matters, so the
// rules here are conservative:
//
//   - Only Allow statements count. A Deny that narrows an Allow is not modelled,
//     so an action may be reported as permitted when a Deny elsewhere blocks it.
//     That errs toward overstating capability, which is the safe direction for a
//     security finding but must be said out loud — it is in the report's
//     limitations section.
//   - Wildcards expand to nothing and are recorded verbatim. `s3:*` is reported
//     as `s3:*`, and the control plane treats it as destructive. Expanding it
//     into a guessed action list would invent findings.
//   - A policy we cannot parse is counted, not skipped silently.

// maxPoliciesPerRole bounds the work per principal. A role with more attached
// policies than this is unusual enough that partial data plus a note beats
// spending a customer's API budget enumerating it.
const maxPoliciesPerRole = 40

// IdentityReader enumerates roles, their tags, and what their policies permit.
type IdentityReader struct {
	API awsread.IdentityAPI
}

type policyDocument struct {
	Statement []struct {
		Effect string          `json:"Effect"`
		Action json.RawMessage `json:"Action"`
	} `json:"Statement"`
}

// Read returns facts for one role ARN.
func (r *IdentityReader) Read(ctx context.Context, roleARN string) (wire.PrincipalFacts, error) {
	name := roleNameFromARN(roleARN)
	if name == "" {
		return wire.PrincipalFacts{}, fmt.Errorf("not a role ARN: %q", roleARN)
	}

	facts := wire.PrincipalFacts{
		Principal:    roleARN,
		AccountID:    arnAccount(roleARN),
		RoleTags:     map[string]string{},
		ResourceTags: map[string]string{},
	}

	role, err := r.API.GetRole(ctx, &iam.GetRoleInput{RoleName: aws.String(name)})
	if err != nil {
		return facts, fmt.Errorf("getting role %s: %w", name, err)
	}
	if role.Role != nil {
		facts.IAMPath = aws.ToString(role.Role.Path)
		for _, tag := range role.Role.Tags {
			facts.RoleTags[aws.ToString(tag.Key)] = aws.ToString(tag.Value)
		}
	}

	// GetRole returns tags only when the caller has iam:ListRoleTags in some
	// partitions; ask explicitly rather than depending on that.
	if tags, err := r.API.ListRoleTags(ctx, &iam.ListRoleTagsInput{
		RoleName: aws.String(name),
	}); err == nil {
		for _, tag := range tags.Tags {
			facts.RoleTags[aws.ToString(tag.Key)] = aws.ToString(tag.Value)
		}
	}

	actions := map[string]bool{}
	r.collectAttached(ctx, name, actions)
	r.collectInline(ctx, name, actions)

	facts.Actions = sortedKeys(actions)
	for _, action := range facts.Actions {
		if strings.EqualFold(action, "sts:AssumeRole") {
			// Lateral movement: the effective blast radius becomes whatever
			// the assumed role can do. The control plane treats this as write
			// capability for that reason.
			facts.AssumableRole = append(facts.AssumableRole, "*")
		}
	}
	return facts, nil
}

func (r *IdentityReader) collectAttached(ctx context.Context, roleName string, into map[string]bool) {
	attached, err := r.API.ListAttachedRolePolicies(ctx, &iam.ListAttachedRolePoliciesInput{
		RoleName: aws.String(roleName),
	})
	if err != nil {
		return
	}

	for i, policy := range attached.AttachedPolicies {
		if i >= maxPoliciesPerRole {
			into["custos:truncated"] = true
			return
		}
		arn := aws.ToString(policy.PolicyArn)
		if arn == "" {
			continue
		}
		meta, err := r.API.GetPolicy(ctx, &iam.GetPolicyInput{PolicyArn: aws.String(arn)})
		if err != nil || meta.Policy == nil {
			continue
		}
		version, err := r.API.GetPolicyVersion(ctx, &iam.GetPolicyVersionInput{
			PolicyArn: aws.String(arn),
			VersionId: meta.Policy.DefaultVersionId,
		})
		if err != nil || version.PolicyVersion == nil {
			continue
		}
		mergeActions(into, aws.ToString(version.PolicyVersion.Document))
	}
}

func (r *IdentityReader) collectInline(ctx context.Context, roleName string, into map[string]bool) {
	names, err := r.API.ListRolePolicies(ctx, &iam.ListRolePoliciesInput{
		RoleName: aws.String(roleName),
	})
	if err != nil {
		return
	}
	for i, policyName := range names.PolicyNames {
		if i >= maxPoliciesPerRole {
			into["custos:truncated"] = true
			return
		}
		out, err := r.API.GetRolePolicy(ctx, &iam.GetRolePolicyInput{
			RoleName:   aws.String(roleName),
			PolicyName: aws.String(policyName),
		})
		if err != nil {
			continue
		}
		mergeActions(into, aws.ToString(out.PolicyDocument))
	}
}

// mergeActions decodes a policy document and adds its Allow actions.
//
// IAM returns policy documents URL-encoded. Forgetting that yields a JSON parse
// error on every policy and a report in which nothing can write, which is the
// most dangerous possible failure: it looks like good news.
func mergeActions(into map[string]bool, document string) {
	if document == "" {
		return
	}
	decoded, err := url.QueryUnescape(document)
	if err != nil {
		decoded = document
	}

	var doc policyDocument
	if err := json.Unmarshal([]byte(decoded), &doc); err != nil {
		into["custos:unparsed-policy"] = true
		return
	}

	for _, statement := range doc.Statement {
		if !strings.EqualFold(statement.Effect, "Allow") {
			// Deny statements are not modelled. See the package comment: this
			// errs toward overstating capability, and the report says so.
			continue
		}
		for _, action := range decodeActions(statement.Action) {
			into[action] = true
		}
	}
}

// decodeActions handles Action being either a string or an array of strings.
func decodeActions(raw json.RawMessage) []string {
	if len(raw) == 0 {
		return nil
	}
	var single string
	if err := json.Unmarshal(raw, &single); err == nil {
		return []string{single}
	}
	var many []string
	if err := json.Unmarshal(raw, &many); err == nil {
		return many
	}
	return nil
}

func roleNameFromARN(arn string) string {
	idx := strings.Index(arn, ":role/")
	if idx < 0 {
		return ""
	}
	path := arn[idx+len(":role/"):]
	// Strip any IAM path: arn:...:role/team/service-role/name -> name
	if slash := strings.LastIndex(path, "/"); slash >= 0 {
		return path[slash+1:]
	}
	return path
}

func sortedKeys(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	// Deterministic output so a re-scan produces a comparable batch.
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}
