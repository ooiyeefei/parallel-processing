# Deployed to Lambda - function triggered by new output.json file uploaded and write to DynamoDB table
import os
import json
import traceback
import boto3
from botocore.exceptions import ClientError
import logging
from decimal import Decimal, InvalidOperation

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['DYNAMODB_TABLE_NAME'])

# output bucket
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET')


def safe_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (TypeError, InvalidOperation):
        return None


def handler(event, context):
    request_id = event['request_id']
    logger.info(f"Processing request ID: {request_id}")

    try:
        # Download and read manifest.json
        manifest_obj = s3_client.get_object(Bucket=OUTPUT_BUCKET,
                                            Key=f"{request_id}/manifest.json")
        manifest_data = json.loads(manifest_obj['Body'].read().decode('utf-8'))
        logger.info(f"Manifest data: {json.dumps(manifest_data)}")

        total_items = 0
        processed_items = 0

        with table.batch_writer() as batch:
            for segment in manifest_data.get('segments', []):
                json_file = segment['segment_file'].replace('.mp4', '.json')
                logger.info(f"Processing JSON file: {json_file}")

                try:
                    json_obj = s3_client.get_object(
                        Bucket=OUTPUT_BUCKET,
                        Key=f"{request_id}/processed_chunks/{json_file}")
                    segment_data = json.loads(
                        json_obj['Body'].read().decode('utf-8'))
                    logger.info(f"Segment data length: {len(segment_data)}")

                    total_items += len(segment_data)

                    for item in segment_data:
                        try:
                            dynamodb_item = {
                                'request_id': item.get('request_id'),
                                'frame_track_id':
                                f"{item.get('frame_id')}#{item.get('track_id')}",
                                'track_id': item.get('track_id'),
                                'frame_id': item.get('frame_id'),
                                'class_name': item.get('class_name'),
                                'class_id': item.get('class_id'),
                                'confidence':
                                safe_decimal(item.get('confidence')),
                                'timestamp':
                                safe_decimal(item.get('timestamp')),
                                'box': json.dumps(item.get('box', [{}])[0])
                            }
                            batch.put_item(Item=dynamodb_item)
                            processed_items += 1

                        except Exception as e:
                            logger.error(f"Error processing item: {item}")
                            logger.error(f"Error details: {str(e)}")

                except Exception as e:
                    logger.error(
                        f"Error processing JSON file {json_file}: {str(e)}")

        logger.info(
            f"Total items processed: {processed_items} out of {total_items}")

        return {
            'statusCode': 200,
            'body': json.dumps('Successfully updated DynamoDB')
        }

    except ClientError as e:
        print(e.response['Error']['Message'])
        return {
            'statusCode': 500,
            'body': json.dumps('Error updating DynamoDB')
        }
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps('Unexpected error occurred')
        }
