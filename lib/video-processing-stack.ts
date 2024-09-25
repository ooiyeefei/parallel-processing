import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { StorageStack } from './stacks/storage-stack';
import { IamStack } from './stacks/iam-stack';
import { EcrStack } from './stacks/ecr-stack';
import { NetworkStack } from './stacks/network-stack';
import { EksStack } from './stacks/eks-stack';
import { ProcessingStack } from './stacks/processing-stack';

interface IamStackProps extends cdk.StackProps {
  inputBucket: s3.IBucket;
  outputBucket: s3.IBucket;
}

export class VideoProcessingStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const storageStack = new StorageStack(this, 'StorageStack');
    const ecrStack = new EcrStack(this, 'EcrStack');
    const networkStack = new NetworkStack(this, 'NetworkStack');

    // Create the IamStack and pass the bucket references
    const iamStack = new IamStack(this, 'IamStack', {
      inputBucket: storageStack.inputBucket,
      outputBucket: storageStack.outputBucket,
      videoSplitRepo: ecrStack.videoSplitRepo,
    });
    
    const eksStack = new EksStack(this, 'EksStack', {
      vpc: networkStack.vpc,
      batchSecurityGroup: networkStack.batchSecurityGroup,
      iamStack: iamStack,
      ecrStack: ecrStack,
    });

    const processingStack = new ProcessingStack(this, 'ProcessingStack', {
      vpc: networkStack.vpc,
      securityGroup: networkStack.batchSecurityGroup,
      inputBucket: storageStack.inputBucket,
      outputBucket: storageStack.outputBucket,
      videoSplitRepo: ecrStack.videoSplitRepo,
      videoSplitVersion: ecrStack.videoSplitVersion,
      trackingJobRepo: ecrStack.trackingJobRepo,
      trackingJobVersion: ecrStack.trackingJobVersion,
      videoMergeRepo: ecrStack.videoMergeRepo,
      videoMergeVersion: ecrStack.videoMergeVersion,
      yoloServiceAddress: eksStack.yoloServiceAddress,
      bytetrackServiceAddress: eksStack.bytetrackServiceAddress,
    });

  }
}
