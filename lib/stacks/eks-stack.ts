import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import { KubectlV30Layer } from '@aws-cdk/lambda-layer-kubectl-v30';
import { Construct } from 'constructs';
import { EcrStack } from './ecr-stack';
import { IamStack } from './iam-stack';

interface EksStackProps extends cdk.StackProps {
  vpc: ec2.Vpc;
  batchSecurityGroup: ec2.SecurityGroup;
  ecrStack: EcrStack;
  iamStack: IamStack;
}

export class EksStack extends cdk.Stack {
  public readonly cluster: eks.Cluster;
  public readonly yoloServiceAddress: string;
  public readonly bytetrackServiceAddress: string;

  constructor(scope: Construct, id: string, props: EksStackProps) {
    super(scope, id, props);

    // creation in eks-stack instead of iam-stack due to cyclic reference
    // IMPT - this allows any users who assumed the role with 'AWSAdministratorAccess' into this account able to access the cluster
    // update as needed.
    const eksMasterRole = new iam.Role(this, 'EksMasterRole', {
        assumedBy: new iam.AccountPrincipal(this.account),
        managedPolicies: [
          iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEKSClusterPolicy")
        ]
      });

      eksMasterRole.assumeRolePolicy?.addStatements(
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          principals: [new iam.AccountPrincipal(this.account)],
          actions: ['sts:AssumeRole'],
          conditions: {
            'StringLike': {
              'aws:PrincipalArn': [
                `arn:aws:iam::${this.account}:assumed-role/AWSReservedSSO_AWSAdministratorAccess*`
              ]
            }
          }
        })
      );
  
      eksMasterRole.attachInlinePolicy(new iam.Policy(this, 'EksClusterCreationPolicy', {
        statements: [
          new iam.PolicyStatement({
            actions: ['eks:*'],
            resources: ['*'],
          })
        ]
      }));

    this.cluster = new eks.Cluster(this, 'VideoProcessingCluster', {
      version: eks.KubernetesVersion.V1_30,
      kubectlLayer: new KubectlV30Layer(this, `kubectl-v30-layer`),
      vpc: props.vpc,
      mastersRole: eksMasterRole,
      albController: {
        version: eks.AlbControllerVersion.V2_8_2,
      },
      defaultCapacity: 0,
      clusterLogging: [
        eks.ClusterLoggingTypes.API,
        eks.ClusterLoggingTypes.AUDIT,
        eks.ClusterLoggingTypes.AUTHENTICATOR,
        eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
        eks.ClusterLoggingTypes.SCHEDULER
      ],
    });

    new eks.CfnAddon(this, 'ContainerInsightsAddon', {
      addonName: 'amazon-cloudwatch-observability',
      clusterName: this.cluster.clusterName,
      resolveConflicts: 'OVERWRITE',
    });

    const eksNodeGroupRole = new iam.Role(this, 'EksNodeGroupRole', {
      assumedBy: new iam.ServicePrincipal("ec2.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEKSWorkerNodePolicy"),
        iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEC2ContainerRegistryReadOnly"),
        iam.ManagedPolicy.fromAwsManagedPolicyName("CloudWatchAgentServerPolicy"),
        iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEKS_CNI_Policy"),
      ]
    });

    this.cluster.addNodegroupCapacity('YoloBytetrackNodeGroup', {
      nodegroupName: "yolo-bytetrack-ng",
      instanceTypes: [new ec2.InstanceType('m5.xlarge')],
      minSize: 1,
      maxSize: 3,
      diskSize: 100,
      desiredSize: 1,
      nodeRole: eksNodeGroupRole
    });

    this.cluster.clusterSecurityGroup.addIngressRule(
      props.batchSecurityGroup,
      ec2.Port.tcp(80),
      'Allow inbound traffic from Batch jobs'
    );

    const yoloServiceAccount = this.cluster.addServiceAccount('YoloServiceAccount', {
        name: 'yolo-service-account',
        namespace: 'default',
      });
      
    props.iamStack.addYoloServiceAccountPolicy(yoloServiceAccount);

    // EKS Deployments and Services
    // yolo
    const yoloDeploymentManifest = this.cluster.addManifest('YoloDeployment', {
        apiVersion: 'apps/v1',
        kind: 'Deployment',
        metadata: { name: 'yolo' },
        spec: {
          replicas: 1,
          selector: { matchLabels: { app: 'yolo' } },
          template: {
            metadata: { labels: { app: 'yolo' } },
            spec: {
              serviceAccountName: yoloServiceAccount.serviceAccountName,
              containers: [{
                name: 'yolo',
                image: props.ecrStack.yoloRepo.repositoryUri + `:${props.ecrStack.yoloVersion}`,
                ports: [{ containerPort: 5000 }],
              }],
            },
          },
        },
      });

      
      //yolo svc
      const yoloSvcManifest = this.cluster.addManifest('YoloService', {
        apiVersion: 'v1',
        kind: 'Service',
        metadata: { 
          name: 'yolo-svc',
          annotations: {
            'service.beta.kubernetes.io/aws-load-balancer-type': 'external',
            'service.beta.kubernetes.io/aws-load-balancer-nlb-target-type': 'ip',
            'service.beta.kubernetes.io/aws-load-balancer-scheme': 'internal',
          }
        },
        spec: {
          type: 'LoadBalancer',
          selector: { app: 'yolo' },
          ports: [{ port: 80, targetPort: 5000 }],
        },
      });
  
      const yoloServiceAddress = new eks.KubernetesObjectValue(this, 'YoloServiceAddress', {
        cluster: this.cluster,
        objectType: 'service',
        objectName: 'yolo-svc',
        jsonPath: '.status.loadBalancer.ingress[0].hostname'
      });
  
      
  
      // bytetrack deployment
      const bytetrackDeploymentManifest = this.cluster.addManifest('BytetrackDeployment', {
        apiVersion: 'apps/v1',
        kind: 'Deployment',
        metadata: { name: 'bytetrack' },
        spec: {
          replicas: 1,
          selector: { matchLabels: { app: 'bytetrack' } },
          template: {
            metadata: { labels: { app: 'bytetrack' } },
            spec: {
              containers: [{
                name: 'bytetrack',
                image: props.ecrStack.bytetrackRepo.repositoryUri + `:${props.ecrStack.bytetrackVersion}`,
                ports: [{ containerPort: 5001 }],
              }],
            },
          },
        },
      });
  
      // bytetrack svc
      const bytetrackSvcManifest = this.cluster.addManifest('BytetrackService', {
        apiVersion: 'v1',
        kind: 'Service',
        metadata: { 
          name: 'bytetrack-svc',
          annotations: {
            'service.beta.kubernetes.io/aws-load-balancer-type': 'external',
            'service.beta.kubernetes.io/aws-load-balancer-nlb-target-type': 'ip',
            'service.beta.kubernetes.io/aws-load-balancer-scheme': 'internal',
          }
        },
        spec: {
          type: 'LoadBalancer',
          selector: { app: 'bytetrack' },
          ports: [{ port: 80, targetPort: 5001 }],
        },
      });
  
      const bytetrackServiceAddress = new eks.KubernetesObjectValue(this, 'BytetrackServiceAddress', {
        cluster: this.cluster,
        objectType: 'service',
        objectName: 'bytetrack-svc',
        jsonPath: '.status.loadBalancer.ingress[0].hostname'
      });

      // // https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_eks-readme.html#:~:text=Every%20Kubernetes%20manifest,be%20done%20explicitly
      if (this.cluster.albController) {
        yoloDeploymentManifest.node.addDependency(this.cluster.albController);
        yoloSvcManifest.node.addDependency(this.cluster.albController);
        bytetrackDeploymentManifest.node.addDependency(this.cluster.albController);
        bytetrackSvcManifest.node.addDependency(this.cluster.albController);
      }
      
      this.yoloServiceAddress = yoloServiceAddress.value;
      this.bytetrackServiceAddress = bytetrackServiceAddress.value;
  }
}