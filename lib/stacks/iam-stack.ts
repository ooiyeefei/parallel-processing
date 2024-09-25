import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';

interface IamStackProps extends cdk.StackProps {
    inputBucket: s3.IBucket;
    outputBucket: s3.IBucket;
    videoSplitRepo: ecr.IRepository;
  }

export class IamStack extends cdk.Stack {
  public readonly eksMasterRole: iam.Role;
  public readonly ecsTaskExecutionRole: iam.Role;
  public readonly ecsTaskRoleVideoSplitJob: iam.Role;
  private readonly inputBucket: s3.IBucket;
  private readonly outputBucket: s3.IBucket;

  constructor(scope: Construct, id: string, props: IamStackProps) {
    super(scope, id, props);

    if (!props.inputBucket || !props.outputBucket) {
        throw new Error('Input and output buckets must be provided to IamStack');
      }

    this.inputBucket = props.inputBucket;
    this.outputBucket = props.outputBucket;
      
  }
  public addYoloServiceAccountPolicy(serviceAccount: iam.IGrantable): void {
    serviceAccount.grantPrincipal.addToPrincipalPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        's3:GetObject',
        's3:ListBucket',
      ],
      resources: [
        this.inputBucket.bucketArn,
        `${this.inputBucket.bucketArn}/*`,
        this.outputBucket.bucketArn,
        `${this.outputBucket.bucketArn}/*`,
      ],
    }));
  }
}

