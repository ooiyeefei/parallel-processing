#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { VideoProcessingStack } from '../lib/video-processing-stack';

const app = new cdk.App();
new VideoProcessingStack(app, 'VideoProcessingStack');
