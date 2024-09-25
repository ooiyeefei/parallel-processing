import * as cdk from 'aws-cdk-lib';
import * as stepfunctions from 'aws-cdk-lib/aws-stepfunctions';
import * as stepfunctions_tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as batch from 'aws-cdk-lib/aws-batch';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import * as path from 'path';

interface ProcessingStackProps extends cdk.StackProps {
    vpc: ec2.Vpc;
    securityGroup: ec2.SecurityGroup;
    inputBucket: s3.IBucket;
    outputBucket: s3.IBucket;
    videoSplitRepo: ecr.IRepository;
    videoSplitVersion: string;
    trackingJobRepo: ecr.IRepository;
    trackingJobVersion: string;
    videoMergeRepo: ecr.IRepository;
    videoMergeVersion: string;
    yoloServiceAddress: string;
    bytetrackServiceAddress: string;
}


export class ProcessingStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props: ProcessingStackProps) {
      super(scope, id, props);

      // Create DDB
      const dynamoTable = new dynamodb.Table(this, 'TrackingResultsTable', {
        partitionKey: { name: 'request_id', type: dynamodb.AttributeType.STRING },
        sortKey: { name: 'frame_track_id', type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      });

      // Create Reset Bytetrack ID Lambda function
      const resetBytetrackerId = new lambda.Function(this, 'ResetBytetrackerId', {
        runtime: lambda.Runtime.PYTHON_3_10,
        handler: 'index.handler',
        vpc: props.vpc,
        code: lambda.Code.fromAsset(path.join(__dirname, '../../app/resetBytetrackerId')),
        timeout: cdk.Duration.seconds(30),
        memorySize: 128, 
        environment: {
          BYTETRACK_SERVICE_ENDPOINT: `http://${props.bytetrackServiceAddress}`,
        }
      });

      
      
      // Create Update DDB Lambda function
      const updateDynamoDbLambda = new lambda.Function(this, 'UpdateDynamoDbLambda', {
        runtime: lambda.Runtime.PYTHON_3_10,
        handler: 'index.handler',
        code: lambda.Code.fromAsset(path.join(__dirname, '../../app/updateDdb')),
        environment: {
          OUTPUT_BUCKET: props.outputBucket.bucketName,
          DYNAMODB_TABLE_NAME: dynamoTable.tableName,
        },
        timeout: cdk.Duration.minutes(5),
        memorySize: 1024,
      });

      // Grant Lambda function read/write permissions to DynamoDB table
      dynamoTable.grantReadWriteData(updateDynamoDbLambda);

      // Grant Lambda function read access to S3 bucket
      props.outputBucket.grantRead(updateDynamoDbLambda);

      // creation in batch-stack instead of iam-stack due to cyclic reference
    const ecsTaskExecutionRole = new iam.Role(this, 'EcsTaskExecutionRole', {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AmazonECSTaskExecutionRolePolicy")
      ]
    });

    if (props.videoSplitRepo) {
        props.videoSplitRepo.grantPull(ecsTaskExecutionRole);
      }

  const ecsTaskRoleVideoProcessingJob = new iam.Role(this, 'EcsTaskRoleVideoProcessingJob', {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });

    ecsTaskRoleVideoProcessingJob.addToPolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          's3:GetObject',
          's3:ListBucket'
        ],
        resources: [
            props.inputBucket.bucketArn,
          `${props.inputBucket.bucketArn}/*`,
          props.outputBucket.bucketArn,
          `${props.outputBucket.bucketArn}/*`
        ]
      }));

      ecsTaskRoleVideoProcessingJob.addToPolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          's3:PutObject',
          's3:ListBucket'
        ],
        resources: [
          `arn:aws:s3:::${props.outputBucket.bucketName}`,
          `arn:aws:s3:::${props.outputBucket.bucketName}/*`
        ]
      }));

      ecsTaskRoleVideoProcessingJob.addToPolicy(new iam.PolicyStatement({
        actions: ['states:SendTaskSuccess', 'states:SendTaskFailure'],
        resources: ['*'],
      }));

      ecsTaskRoleVideoProcessingJob.addToPolicy(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'eks:DescribeCluster',
          'eks:ListClusters',
          'eks:DescribeNodegroup',
          'eks:ListNodegroups',
          'eks:ListUpdates',
          'eks:AccessKubernetesApi'
        ],
        resources: ['*'],
      }));

  const batchComputeEnvironment = new batch.FargateComputeEnvironment(this, 'BatchFargateComputeEnv', {
    vpc: props.vpc,
    securityGroups: [props.securityGroup],
    replaceComputeEnvironment: true,
  });

  const createJobQueue = (name: string, priority: number) => {
    return new batch.JobQueue(this, `${name}JobQueue`, {
      computeEnvironments: [
        {
          computeEnvironment: batchComputeEnvironment,
          order: 1,
        },
      ],
      priority: priority,
    });
  };

  // Diff job types (split, main tracking-job, merge) may have varying resource requirements and priorities. 
  // Separating them into diff Qs allows for better resource allocation and prioritization.
  // and preventing one job type from grabbing resources and potentially exhausting out available resources for other job types.
  const videoSplitQueue = createJobQueue('VideoSplit', 1);
  const trackingJobQueue = createJobQueue('TrackingJob', 2);
  // const videoMergeQueue = createJobQueue('VideoMerge', 3);
  const videoAnnotateQueue = createJobQueue('VideoAnnotate', 3);

  // Video split job
  // with AWS Batch ECS fargate
  const videoSplitContainerDef = new batch.EcsFargateContainerDefinition(this, 'VideoSplitContainerDefinition', {
      image: ecs.ContainerImage.fromEcrRepository(props.videoSplitRepo, props.videoSplitVersion),
      cpu: 4,
      memory: cdk.Size.gibibytes(8),
      environment: {
        OUTPUT_BUCKET: props.outputBucket.bucketName,
        SEGMENT_DURATION: '5'
      },
      executionRole: ecsTaskExecutionRole,
      jobRole: ecsTaskRoleVideoProcessingJob,
      logging: new ecs.AwsLogDriver({
        streamPrefix: 'video-split',
        logRetention: logs.RetentionDays.ONE_WEEK
      })
    });
  
    const videoSplitJobDef = new batch.EcsJobDefinition(this, 'VideoSplitJobDefinition', {
      container: videoSplitContainerDef,
    });

    const videoSplittingBatchJob = new stepfunctions_tasks.BatchSubmitJob(this, 'VideoSplittingBatchJob', {
      jobName: 'video-split-job',
      jobDefinitionArn: videoSplitJobDef.jobDefinitionArn,
      jobQueueArn: videoSplitQueue.jobQueueArn,
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      resultPath: '$.splitResult',
      containerOverrides: {
        environment: {
          INPUT_BUCKET: stepfunctions.JsonPath.stringAt('$.input_bucket_name'),
          INPUT_VIDEO: stepfunctions.JsonPath.stringAt('$.original_input_video'),
          REQUEST_ID: stepfunctions.JsonPath.stringAt('$.request_id'),
          TASK_TOKEN: stepfunctions.JsonPath.taskToken
        }
      },
    });


    // with ECS Fargate task directly
    const ecsCluster = new ecs.Cluster(this, 'VideoProcessingEcsCluster', {
      clusterName: 'video-processing-cluster',
      vpc: props.vpc,
      containerInsights: true
    })

    const videoSplitTaskDefinition = new ecs.FargateTaskDefinition(this, 'VideoSplitTaskDefinition', {
      cpu: 4096,
      memoryLimitMiB: 8192,
      taskRole: ecsTaskRoleVideoProcessingJob,
      executionRole: ecsTaskExecutionRole
    });
    
    videoSplitTaskDefinition.addContainer('VideoSplitContainer', {
      image: ecs.ContainerImage.fromEcrRepository(props.videoSplitRepo, props.videoSplitVersion),
      logging: new ecs.AwsLogDriver({
        streamPrefix: 'video-split',
        logRetention: logs.RetentionDays.ONE_WEEK
      }),
      environment: {
        OUTPUT_BUCKET: props.outputBucket.bucketName,
        SEGMENT_DURATION: '5'
      }
    });

    const videoSplittingEcsTask = new stepfunctions_tasks.EcsRunTask(this, 'VideoSplittingEcsTask', {
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      cluster: ecsCluster,
      taskDefinition: videoSplitTaskDefinition,
      launchTarget: new stepfunctions_tasks.EcsFargateLaunchTarget(),
      containerOverrides: [
        {
          containerDefinition: videoSplitTaskDefinition.defaultContainer!,
          environment: [
            { name: 'INPUT_BUCKET', value: stepfunctions.JsonPath.stringAt('$.input_bucket_name') },
            { name: 'INPUT_VIDEO', value: stepfunctions.JsonPath.stringAt('$.original_input_video') },
            { name: 'REQUEST_ID', value: stepfunctions.JsonPath.stringAt('$.request_id') }
          ]
        }
      ],
      resultPath: '$.splitResult'
    });

  // video annotation job

  // with Lambda - tested but lambda does not support opencv, need to build with lambda layer / custom libs
  // const videoAnnotationLambdaTask = new stepfunctions_tasks.LambdaInvoke(this, 'VideoAnnotationLambdaTask', {
  //   lambdaFunction: new lambda.Function(this, 'VideoAnnotationLambda', {
  //     runtime: lambda.Runtime.PYTHON_3_10,
  //     handler: 'index.handler',
  //     code: lambda.Code.fromAsset(path.join(__dirname, '../../app/video-annotation')),
  //     timeout: cdk.Duration.minutes(5),
  //     memorySize: 3008,
  //     environment: {
  //       INPUT_BUCKET: props.inputBucket.bucketName,
  //       OUTPUT_BUCKET: props.outputBucket.bucketName
  //     }
  //   }),
  //   resultPath: '$.annotationResult',
  //   payload: stepfunctions.TaskInput.fromObject({
  //     'request_id.$': '$.request_id',
  //     'input_bucket.$': '$.input_bucket_name',
  //     'output_bucket.$': '$.output_bucket_name',
  //   }),
  // });

  
  // with AWS Batch ECS fargate
  const videoAnnotationContainerDef = new batch.EcsFargateContainerDefinition(this, 'VideoAnnotationContainerDef', {
    image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../../app/video-annotation')),
    cpu: 4,
    memory: cdk.Size.gibibytes(8),
    environment: {
      INPUT_BUCKET: props.inputBucket.bucketName,
      OUTPUT_BUCKET: props.outputBucket.bucketName,
    },
    executionRole: ecsTaskExecutionRole,
    jobRole: ecsTaskRoleVideoProcessingJob,
    logging: new ecs.AwsLogDriver({
      streamPrefix: 'video-annotation',
      logRetention: logs.RetentionDays.ONE_WEEK
    })
  });
  
  const videoAnnotationJobDef = new batch.EcsJobDefinition(this, 'VideoAnnotationJobDefinition', {
    container: videoAnnotationContainerDef,
  });

  const videoAnnotationBatchJob = new stepfunctions_tasks.BatchSubmitJob(this, 'VideoAnnotationBatchJob', {
    jobName: 'video-annotation-job',
    jobDefinitionArn: videoAnnotationJobDef.jobDefinitionArn,
    jobQueueArn: videoAnnotateQueue.jobQueueArn,
    integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
    containerOverrides: {
      environment: {
        REQUEST_ID: stepfunctions.JsonPath.stringAt('$.request_id'),
        OUTPUT_BUCKET: stepfunctions.JsonPath.stringAt('$.output_bucket_name'),
        INPUT_BUCKET: stepfunctions.JsonPath.stringAt('$.input_bucket_name'),
        INPUT_VIDEO: stepfunctions.JsonPath.stringAt('$.original_input_video')
      }
    },
    resultPath: '$.annotationResult'
  });

  // Tracking job
  const trackingJobContainerDef = new batch.EcsFargateContainerDefinition(this, 'TrackingJobContainerDefinition', {
      image: ecs.ContainerImage.fromEcrRepository(props.trackingJobRepo, props.trackingJobVersion),
      cpu: 2,
      memory: cdk.Size.gibibytes(4),
      environment: {
      YOLO_SERVICE_ENDPOINT: `http://${props.yoloServiceAddress}`,
      BYTETRACK_SERVICE_ENDPOINT: `http://${props.bytetrackServiceAddress}`,
      OUTPUT_BUCKET: props.outputBucket.bucketName,
      },
      executionRole: ecsTaskExecutionRole,
      jobRole: ecsTaskRoleVideoProcessingJob,
      logging: new ecs.AwsLogDriver({
      streamPrefix: 'tracking-job',
      logRetention: logs.RetentionDays.ONE_WEEK
      })
  });

  const trackingJobDef = new batch.EcsJobDefinition(this, 'TrackingJobDefinition', {
      container: trackingJobContainerDef  
  });

  // changing method from merging chunks to annotating separately
  // // Video Merge Job
  // const videoMergeContainerDef = new batch.EcsFargateContainerDefinition(this, 'VideoMergeContainerDefinition', {
  //     image: ecs.ContainerImage.fromEcrRepository(props.videoMergeRepo, props.videoMergeVersion),
  //     cpu: 4,
  //     memory: cdk.Size.gibibytes(8),
  //     environment: {
  //     OUTPUT_BUCKET: props.outputBucket.bucketName,
  //     },
  //     executionRole: ecsTaskExecutionRole,
  //     jobRole: ecsTaskRoleVideoProcessingJob,
  //     logging: new ecs.AwsLogDriver({
  //     streamPrefix: 'video-merge',
  //     logRetention: logs.RetentionDays.ONE_WEEK
  //     })
  // });

  // const videoMergeJobDef = new batch.EcsJobDefinition(this, 'VideoMergeJobDefinition', {
  //     container: videoMergeContainerDef,
  // });
  
      const resetBytetrackerIdTask = new stepfunctions_tasks.LambdaInvoke(this, 'ResetBytetrackerIdTask', {
        lambdaFunction: resetBytetrackerId,
        retryOnServiceExceptions: true,
      })

      const parallelPreProcess = new stepfunctions.Parallel(this, 'ParallelPreProcess', {
        resultPath: '$.parallelResults'
      })
        .branch(videoSplittingBatchJob)
        .branch(resetBytetrackerIdTask);

        const filterParallelResults = new stepfunctions.Pass(this, 'FilterParallelResults', {
          parameters: {
            'input_bucket_name.$': '$.input_bucket_name',
            'output_bucket_name.$': '$.output_bucket_name',
            'original_input_video.$': '$.original_input_video',
            'request_id.$': '$.request_id',
            'splitResult.$': '$.parallelResults[0].splitResult'
          }
        });
  
      const processVideoChunks = new stepfunctions.Map(this, 'ProcessVideoChunks', {
        itemsPath: stepfunctions.JsonPath.stringAt('$.splitResult.segments'),
        itemSelector: {
          'input_bucket_name.$': '$.input_bucket_name',
          'output_bucket.$': '$.output_bucket_name',
          'request_id.$': '$.request_id',
          'original_input_video.$': '$.original_input_video',
          'segment.$': '$$.Map.Item.Value'
        },
        resultPath: stepfunctions.JsonPath.DISCARD,
        maxConcurrency: 10,
      });

      processVideoChunks.addRetry({
        maxAttempts: 2,
        maxDelay: cdk.Duration.seconds(5),
      });
      
      // 'Qn' - to find out catch errors and fallback?
  
      const trackingJobBatchJob = new stepfunctions_tasks.BatchSubmitJob(this, 'TrackingJobBatchJob', {
        jobName: stepfunctions.JsonPath.format('tracking-job-{}', stepfunctions.JsonPath.stringAt('$.segment.segment_number')),
        jobDefinitionArn: trackingJobDef.jobDefinitionArn,
        jobQueueArn: trackingJobQueue.jobQueueArn,
        integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
        containerOverrides: {
          environment: {
            INPUT_BUCKET: stepfunctions.JsonPath.stringAt('$.input_bucket_name'),
            INPUT_VIDEO: stepfunctions.JsonPath.format('{}/split_chunks/{}', stepfunctions.JsonPath.stringAt('$.request_id'), stepfunctions.JsonPath.stringAt('$.segment.segment_file')),
            REQUEST_ID: stepfunctions.JsonPath.stringAt('$.request_id')
          }
        }
      });
  
      processVideoChunks.itemProcessor(trackingJobBatchJob);
  
      // const videoMergeBatchJob = new stepfunctions_tasks.BatchSubmitJob(this, 'VideoMergeBatchJob', {
      //   jobName: 'video-merge-job',
      //   jobDefinitionArn: videoMergeJobDef.jobDefinitionArn,
      //   jobQueueArn: videoMergeQueue.jobQueueArn,
      //   integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      //   containerOverrides: {
      //     environment: {
      //       REQUEST_ID: stepfunctions.JsonPath.stringAt('$.request_id')
      //     }
      //   }
      // });
  
      const initVariables = new stepfunctions.Pass(this, 'InitVariables', {
        parameters: {
          'input_bucket_name.$': '$.detail.bucket.name',
          'output_bucket_name': props.outputBucket.bucketName,
          'original_input_video.$': '$.detail.object.key',
          'request_id.$': '$.detail.request-id'
        }
      });

      const updateDdbLambdaTask = new stepfunctions_tasks.LambdaInvoke(this, 'UpdateDynamoDb', {
        lambdaFunction: updateDynamoDbLambda,
        retryOnServiceExceptions: true, // 'Qn' - if added 'parallelExecution.addRetry', will this be unnecessary? Handle in lambda retry or stepfn better?
        payload: stepfunctions.TaskInput.fromObject({
          request_id: stepfunctions.JsonPath.stringAt('$.request_id'),
          output_bucket: props.outputBucket.bucketName,
        }),
      })

      const parallelPostProcess = new stepfunctions.Parallel(this, 'ParallelPostProcess', {
        resultPath: '$.postProcessResults',
      })
        .branch(videoAnnotationBatchJob)
        .branch(updateDdbLambdaTask);

        parallelPostProcess.addRetry({
          maxAttempts: 2,
          maxDelay: cdk.Duration.seconds(5),
        });

      const chain = initVariables
          .next(parallelPreProcess)
          .next(filterParallelResults)
          .next(processVideoChunks)
          .next(new stepfunctions.Pass(this, 'PrepareForPostProcess', {
            parameters: {
              'request_id.$': '$.request_id',
              'input_bucket_name.$': '$.input_bucket_name',
              'output_bucket_name.$': '$.output_bucket_name',
              'original_input_video.$': '$.original_input_video',
            },
          }))
          .next(parallelPostProcess)
          .next(new stepfunctions.Succeed(this, 'WorkflowComplete'));
      
      const workflow = new stepfunctions.StateMachine(this, 'VideoProcessingWorkflow', {
        definitionBody: stepfunctions.DefinitionBody.fromChainable(chain),
        timeout: cdk.Duration.minutes(8),
      });
  
      videoSplitJobDef.grantSubmitJob(workflow.role, videoSplitQueue);
      trackingJobDef.grantSubmitJob(workflow.role, trackingJobQueue);
      // changing method from merging chunks to annotating separately
      // videoMergeJobDef.grantSubmitJob(workflow.role, videoMergeQueue);
      videoAnnotationJobDef.grantSubmitJob(workflow.role, videoAnnotateQueue);
      workflow.grantStartExecution(new iam.ServicePrincipal('events.amazonaws.com'));
  
      const rule = new events.Rule(this, 'S3NewUploadRule', {
        eventPattern: {
          source: ['aws.s3'],
          detailType: ['Object Created'],
          detail: {
            bucket: {
              name: [props.inputBucket.bucketName]
            }
          }
        }
      });
      
      rule.addTarget(new targets.SfnStateMachine(workflow));
    }
  }