# Copyright Amazon.com and its affiliates; all rights reserved. This file is Amazon Web Services Content and may not be duplicated or distributed without permission.
# SPDX-License-Identifier: MIT-0
import aws_cdk as cdk
import aws_cdk.aws_codebuild as CodeBuild
import aws_cdk.aws_codepipeline as CodePipeline
import aws_cdk.aws_iam as iam
import aws_cdk.aws_logs as logs
import aws_cdk.aws_s3 as s3
import aws_cdk.pipelines as Pipelines
from cdk_nag import AwsSolutionsChecks, NagSuppressions
from constructs import Construct

from .configuration import (
    ACCOUNT_ID,
    DEPLOYMENT,
    GITHUB_REPOSITORY_NAME,
    GITHUB_REPOSITORY_OWNER_NAME,
    GITHUB_TOKEN_NAME,
    PROD,
    TEST,
    get_all_configurations,
    get_logical_id_prefix,
    get_resource_name_prefix,
)
from .pipeline_deploy_stage import PipelineDeployStage


class PipelineStack(cdk.Stack):

    def __init__(
        self, scope: Construct, construct_id: str,
        target_environment: str, target_branch: str, target_aws_env: dict,
        **kwargs
    ):
        """CloudFormation stack to create CDK Pipeline resources (Code Pipeline, Code Build, and ancillary resources).

        Parameters
        ----------
        scope
            Parent of this stack, usually an App or a Stage, but could be any construct
        construct_id
            The construct ID of this stack; if stackName is not explicitly defined,
            this ID (and any parent IDs) will be used to determine the physical ID of the stack
        target_environment
            The target environment for stacks in the deploy stage
        target_branch
            The source branch for polling
        target_aws_env
            The CDK env variables used for stacks in the deploy stage
        kwargs: optional
            Optional keyword arguments to pass up to parent Stack class
        """
        super().__init__(scope, construct_id, **kwargs)

        self.mappings = get_all_configurations()

        self.logical_id_prefix = get_logical_id_prefix()
        self.resource_name_prefix = get_resource_name_prefix()
        self.target_branch = target_branch

        if (target_environment == PROD or target_environment == TEST):
            self.removal_policy = cdk.RemovalPolicy.RETAIN
            self.log_retention = logs.RetentionDays.SIX_MONTHS
        else:
            self.removal_policy = cdk.RemovalPolicy.DESTROY
            self.log_retention = logs.RetentionDays.ONE_MONTH

        self.create_environment_pipeline(
            target_environment,
            target_aws_env
        )


    def create_environment_pipeline(
        self,
        target_environment: str, target_aws_env: dict
    ):
        """Creates CloudFormation stack to create CDK Pipeline resources such as:
        Code Pipeline, Code Build, and ancillary resources.

        Parameters
        ----------
        target_environment
            The target environment for stacks in the deploy stage
        target_branch
            The source branch for polling
        target_aws_env
            The CDK env variables used for stacks in the deploy stage
        """
        code_build_env = CodeBuild.BuildEnvironment(
            build_image=CodeBuild.LinuxBuildImage.STANDARD_7_0,
            privileged=False
        )

        code_build_opt = Pipelines.CodeBuildOptions(
            build_environment=code_build_env,
            role_policy=[
                iam.PolicyStatement(
                    sid='InfrastructurePipelineSecretsManagerPolicy',
                    effect=iam.Effect.ALLOW,
                    actions=[
                        'secretsmanager:GetSecretValue',
                    ],
                    resources=[
                        f'arn:aws:secretsmanager:{self.region}:{self.account}:secret:/InsuranceLake/*',
                    ],
                ),
                iam.PolicyStatement(
                    actions=[ 'sts:AssumeRole' ],
                    resources=[ '*' ],
                    conditions={
                        'StringEquals': {
                            'iam:ResourceTag/aws-cdk:bootstrap-role': 'lookup'
                        }
                    },
                ),
            ]
        )

        code_pipeline=CodePipeline.Pipeline(
                self,
                f'{target_environment}{self.logical_id_prefix}Pipeline',
                pipeline_name=f'{target_environment.lower()}-{self.resource_name_prefix}-infrastructure-pipeline',
                pipeline_type=CodePipeline.PipelineType.V2,
                execution_mode=CodePipeline.ExecutionMode.QUEUED,
                cross_account_keys=True
            )

        pipeline = Pipelines.CodePipeline(
            self,
            f'{target_environment}{self.logical_id_prefix}InfrastructurePipeline',
            code_build_defaults=code_build_opt,
            code_pipeline=code_pipeline,
            self_mutation=True,
            synth=Pipelines.ShellStep(
                'Synth',
                input=self.get_codepipeline_source(),
                commands=[
                    'npm install -g aws-cdk',
                    'python -m pip install -r requirements.txt --root-user-action=ignore',
                    'cdk synth',
                ],
                primary_output_directory='cdk.out',
            ),
            #cross_account_keys=True
        )

        pipeline_deploy_stage = PipelineDeployStage(
                self,
                target_environment,
                target_environment=target_environment,
                deployment_account_id=self.mappings[DEPLOYMENT][ACCOUNT_ID],
                env=cdk.Environment(
                    account=target_aws_env['account'],
                    region=target_aws_env['region']
                )
            )

        # Enable CDK Nag for environment stacks before adding to
        # pipeline, which are deployed with CodePipeline
        cdk.Aspects.of(pipeline_deploy_stage).add(AwsSolutionsChecks())

        pipeline.add_stage(pipeline_deploy_stage)

        # Force Pipeline construct creation during synth so we can add
        # Nag Suppressions, artifact bucket policies, and access Build stages
        pipeline.build_pipeline()

        # Loop through Stages and Actions looking for Build actions
        # that write to CloudWatch logs
        for stage in pipeline.pipeline.stages:
            for action in stage.actions:
                if action.action_properties.category == CodePipeline.ActionCategory.BUILD:
                    logs.LogGroup(
                        self,
                        id=f'CodeBuildAction{action.action_properties.action_name}LogGroup',
                        # Name the log after the project name so it matches where CodeBuild writes
                        # resource object is a PipelineProject
                        log_group_name=f'/aws/codebuild/{action.action_properties.resource.project_name}',
                        removal_policy=self.removal_policy,
                        retention=self.log_retention,
                    )

        # Apply stack removal policy to Artifact Bucket
        pipeline.pipeline.artifact_bucket.apply_removal_policy(self.removal_policy)

        # Enable server access logs in the same bucket using escape hatch
        cfn_artifact_bucket = pipeline.pipeline.artifact_bucket.node.default_child
        cfn_artifact_bucket.logging_configuration = s3.CfnBucket.LoggingConfigurationProperty(
            # TODO: Convert to separate bucket that is part of the Pipeline stack
            log_file_prefix='access-logs'
        )
        # Enable artifact bucket encryption key rotation using escape hatch
        cfn_artifact_bucket_encryption_key = pipeline.pipeline.artifact_bucket.encryption_key.node.default_child
        cfn_artifact_bucket_encryption_key.enable_key_rotation = True
        # Enable artifact bucket versioning
        cfn_artifact_bucket.add_property_override('VersioningConfiguration.Status', 'Enabled')

        # Apply Nag Suppression to all Pipeline resources (many role and policies)
        NagSuppressions.add_resource_suppressions(pipeline, [
            {
                'id': 'AwsSolutions-IAM5',
                'reason': 'Wildcard IAM permissions are used by auto-created Codepipeline policies and custom policies to allow flexible creation of resources'
            },
        ], apply_to_children=True)

        NagSuppressions.add_resource_suppressions(code_pipeline, [
            {
                'id': 'AwsSolutions-IAM5',
                'reason': 'Wildcard IAM permissions are used by auto-created Codepipeline policies and custom policies to allow flexible creation of resources'
            },
        ], apply_to_children=True)


    def get_codepipeline_source(self) -> Pipelines.CodePipelineSource:
        """Based on configuration, create a CodePipeline source object for the selected repository type

        Returns
        -------
        Pipelines.CodePipelineSource
            CodePipeline source repository object
        """
        if self.mappings[DEPLOYMENT][GITHUB_REPOSITORY_NAME]:
            return Pipelines.CodePipelineSource.connection(
                repo_string=f'{self.mappings[DEPLOYMENT][GITHUB_REPOSITORY_OWNER_NAME]}/'
                f'{self.mappings[DEPLOYMENT][GITHUB_REPOSITORY_NAME]}',
                branch=self.target_branch,
                action_name='Source',
                connection_arn="arn:aws:codestar-connections:us-east-1:787127824249:connection/ac69c4b3-c806-4b73-9bb8-df7c3a9b6162",
                trigger_on_push=True,
            )



# Github
            # return Pipelines.CodePipelineSource.git_hub(
            #         repo_string=f'{self.mappings[DEPLOYMENT][GITHUB_REPOSITORY_OWNER_NAME]}/'
            #             f'{self.mappings[DEPLOYMENT][GITHUB_REPOSITORY_NAME]}',
            #         branch=self.target_branch,
            #         action_name='Source',
            #         authentication=cdk.SecretValue.secrets_manager(
            #             self.mappings[DEPLOYMENT][GITHUB_TOKEN_NAME]
            #         ),
            #         trigger=CodePipelineActions.GitHubTrigger.POLL,
            #     )