import os
import json
import urllib3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BYTETRACK_SERVICE_ENDPOINT = os.environ['BYTETRACK_SERVICE_ENDPOINT']


def handler(event, context):
    http = urllib3.PoolManager()

    logger.info(
        f"Attempting to reset ByteTrack IDs at endpoint: {BYTETRACK_SERVICE_ENDPOINT}"
    )

    try:
        response = http.request('POST',
                                f"{BYTETRACK_SERVICE_ENDPOINT}/reset_ids")

        logger.info(f"Received response with status code: {response.status}")

        if response.status == 200:
            logger.info("ByteTrack IDs have been reset successfully.")
            return {
                'statusCode': 200,
                'body': json.dumps('ByteTrack IDs reset successfully')
            }
        else:
            logger.error(
                f"Failed to reset ByteTrack IDs. Status code: {response.status}"
            )
            return {
                'statusCode':
                response.status,
                'body':
                json.dumps(
                    f'Failed to reset ByteTrack IDs. Status code: {response.status}'
                )
            }
    except Exception as e:
        logger.error(f"Error resetting ByteTrack IDs: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps(f'Error: {str(e)}')}
