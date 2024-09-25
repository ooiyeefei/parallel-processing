import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { DockerImageAsset } from 'aws-cdk-lib/aws-ecr-assets';
import * as ecrdeploy from 'cdk-ecr-deployment';
import { Construct } from 'constructs';
import * as path from 'path';

export class EcrStack extends cdk.Stack {
  public readonly videoSplitRepo: ecr.Repository;
  public readonly videoMergeRepo: ecr.Repository;
  public readonly trackingJobRepo: ecr.Repository;
  public readonly yoloRepo: ecr.Repository;
  public readonly bytetrackRepo: ecr.Repository;
  public readonly videoSplitVersion: string;
  public readonly videoMergeVersion: string;
  public readonly trackingJobVersion: string;
  public readonly yoloVersion: string;
  public readonly bytetrackVersion: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const createRepoAndDeploy = (name: string, imagePath: string, versionName: string) => {
      const repo = new ecr.Repository(this, `${name}Repo`, {
        repositoryName: name,
        imageTagMutability: ecr.TagMutability.IMMUTABLE,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        emptyOnDelete: true
      });

      const image = new DockerImageAsset(this, `${name}Image`, {
        directory: path.join(__dirname, '../../app', imagePath)
      });

      const version = `${Math.floor(Date.now() / 1000)}-${versionName}`;

      new ecrdeploy.ECRDeployment(this, `Deploy${name}Image`, {
        src: new ecrdeploy.DockerImageName(image.imageUri),
        dest: new ecrdeploy.DockerImageName(`${repo.repositoryUri}:${version}`),
      });

      return { repo, version };
    };

    const { repo: videoSplitRepo, version: videoSplitVersion } = createRepoAndDeploy('video-split', 'video-split', 'vs');
    const { repo: videoMergeRepo, version: videoMergeVersion } = createRepoAndDeploy('video-merge', 'video-merge', 'vm');
    const { repo: trackingJobRepo, version: trackingJobVersion } = createRepoAndDeploy('tracking-job', 'tracking-job', 'tj');
    const { repo: yoloRepo, version: yoloVersion } = createRepoAndDeploy('yolo', 'yolo', 'yolo');
    const { repo: bytetrackRepo, version: bytetrackVersion } = createRepoAndDeploy('bytetrack', 'bytetrack', 'bytetrack');

    this.videoSplitRepo = videoSplitRepo;
    this.videoMergeRepo = videoMergeRepo;
    this.trackingJobRepo = trackingJobRepo;
    this.yoloRepo = yoloRepo;
    this.bytetrackRepo = bytetrackRepo;
    this.videoSplitVersion = videoSplitVersion;
    this.videoMergeVersion = videoMergeVersion;
    this.trackingJobVersion = trackingJobVersion;
    this.yoloVersion = yoloVersion;
    this.bytetrackVersion = bytetrackVersion;
  }
}