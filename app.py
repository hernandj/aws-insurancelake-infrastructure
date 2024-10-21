# !/usr/bin/env python3
# Copyright Amazon.com and its affiliates; all rights reserved. This file is Amazon Web Services Content and may not be duplicated or distributed without permission.
# SPDX-License-Identifier: MIT-0
import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks, NagSuppressions

from lib.configuration import (
    ACCOUNT_ID,
    CODE_BRANCH,
    DEPLOYMENT,
    DEV,
    PROD,
    REGION,
    TEST,
    get_all_configurations,
    get_logical_id_prefix,
)
from lib.stacks.empty_stack import EmptyStack
from lib.stacks.pipeline_stack import PipelineStack
from lib.stacks.tagging import tag

app = cdk.App()

# Enable CDK Nag for the Mirror repository, Pipeline, and related stacks
# Environment stacks must be enabled on the Stage resource
cdk.Aspects.of(app).add(AwsSolutionsChecks())

if bool(os.environ.get('IS_BOOTSTRAP')):
    EmptyStack(app, 'StackStub')
else:
    raw_mappings = get_all_configurations()

    deployment_account = raw_mappings[DEPLOYMENT][ACCOUNT_ID]
    deployment_region = raw_mappings[DEPLOYMENT][REGION]
    deployment_aws_env = {
        'account': deployment_account,
        'region': deployment_region,
    }
    logical_id_prefix = get_logical_id_prefix()

    if os.environ.get('ENV', DEV) == DEV:
        target_environment = DEV
        dev_account = raw_mappings[DEV][ACCOUNT_ID]
        dev_region = raw_mappings[DEV][REGION]
        dev_aws_env = {
            'account': dev_account,
            'region': dev_region,
        }
        dev_pipeline_stack = PipelineStack(
            app,
            f'{target_environment}-{logical_id_prefix}InfrastructurePipeline',
            description=f'InsuranceLake stack for Infrastructure pipeline - {DEV} environment (SO9489) (uksb-1tu7mtee2)',
            target_environment=DEV,
            target_branch=raw_mappings[DEV][CODE_BRANCH],
            target_aws_env=dev_aws_env,
            env=deployment_aws_env,
        )
        tag(dev_pipeline_stack, DEPLOYMENT)

    if os.environ.get('ENV', TEST) == TEST:
        target_environment = TEST
        test_account = raw_mappings[TEST][ACCOUNT_ID]
        test_region = raw_mappings[TEST][REGION]
        test_aws_env = {
            'account': test_account,
            'region': test_region,
        }
        test_pipeline_stack = PipelineStack(
            app,
            f'{target_environment}-{logical_id_prefix}InfrastructurePipeline',
            description=f'InsuranceLake stack for Infrastructure pipeline - {TEST} environment (SO9489) (uksb-1tu7mtee2)',
            target_environment=TEST,
            target_branch=raw_mappings[TEST][CODE_BRANCH],
            target_aws_env=test_aws_env,
            env=deployment_aws_env,
        )
        tag(test_pipeline_stack, DEPLOYMENT)

    if os.environ.get('ENV', PROD) == PROD:
        target_environment = PROD
        prod_account = raw_mappings[PROD][ACCOUNT_ID]
        prod_region = raw_mappings[PROD][REGION]
        prod_aws_env = {
            'account': prod_account,
            'region': prod_region,
        }
        prod_pipeline_stack = PipelineStack(
            app,
            f'{target_environment}-{logical_id_prefix}InfrastructurePipeline',
            description=f'InsuranceLake stack for Infrastructure pipeline - {PROD} environment (SO9489) (uksb-1tu7mtee2)',
            target_environment=PROD,
            target_branch=raw_mappings[PROD][CODE_BRANCH],
            target_aws_env=prod_aws_env,
            env=deployment_aws_env,
        )
        tag(prod_pipeline_stack, DEPLOYMENT)

    # TODO: Modify replication bucket to have access logs and key rotation
    # Apply tagging to cross-region support stacks
    for stack in app.node.children:
        # All other stacks in the app are custom constructs
        if type(stack) == cdk.Stack:
            # Use the deployment environment for tagging because there
            # is no way to determine 1:1 which pipeline created the stack
            tag(stack, DEPLOYMENT)

            NagSuppressions.add_resource_suppressions(stack, [
                {
                    'id': 'AwsSolutions-S1',
                    'reason': 'Cross-region support stack and bucket are auto-created by Codepipeline'
                },
                {
                    'id': 'AwsSolutions-KMS5',
                    'reason': 'Cross-region support stack and bucket are auto-created by Codepipeline'
                },
            ], apply_to_children=True)

app.synth()