// Package awsclient builds real AWS clients from the ambient credential chain.
//
// Kept separate from the collection logic so that everything in `ingest` is
// testable against fakes, and so that this file — the only place a real AWS
// connection is created — is short enough to read in one sitting.
package awsclient

import (
	"context"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials/stscreds"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	"github.com/aws/aws-sdk-go-v2/service/iam"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/sts"
)

// Options describes how to reach the account being scanned.
type Options struct {
	Region string

	// RoleARN is the cross-account role to assume. Empty means use the ambient
	// credentials directly, which is the mode for a customer running the
	// collector inside their own account.
	RoleARN string

	// ExternalID guards against the confused deputy problem. Required whenever
	// RoleARN is set — a cross-account role without one can be assumed by
	// anyone who learns the ARN.
	ExternalID string
}

// Clients is the set of AWS clients the collector uses.
type Clients struct {
	Logs     *cloudwatchlogs.Client
	Objects  *s3.Client
	Network  *ec2.Client
	Identity *iam.Client
}

// New builds clients, assuming a cross-account role when one is configured.
func New(ctx context.Context, opts Options) (*Clients, error) {
	cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion(opts.Region))
	if err != nil {
		return nil, fmt.Errorf("loading AWS configuration: %w", err)
	}

	if opts.RoleARN != "" {
		if opts.ExternalID == "" {
			return nil, fmt.Errorf(
				"an external ID is required when assuming %s: a cross-account role "+
					"without one can be assumed by anyone who learns the ARN", opts.RoleARN)
		}
		provider := stscreds.NewAssumeRoleProvider(
			sts.NewFromConfig(cfg), opts.RoleARN,
			func(o *stscreds.AssumeRoleOptions) {
				o.ExternalID = aws.String(opts.ExternalID)
				o.RoleSessionName = "custos-collector"
			},
		)
		cfg.Credentials = aws.NewCredentialsCache(provider)
	}

	return &Clients{
		Logs:     cloudwatchlogs.NewFromConfig(cfg),
		Objects:  s3.NewFromConfig(cfg),
		Network:  ec2.NewFromConfig(cfg),
		Identity: iam.NewFromConfig(cfg),
	}, nil
}
