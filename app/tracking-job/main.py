# Main tracking service with cloud hosting - with AWS
import cv2
import requests
import json
import os
import sys
import boto3
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Environment variables
INPUT_BUCKET = os.environ.get('INPUT_BUCKET')
INPUT_VIDEO = os.environ.get('INPUT_VIDEO')
# INPUT_METADATA = os.environ.get('INPUT_METADATA')
REQUEST_ID = os.environ.get('REQUEST_ID')

# output bucket
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET')

# Service endpoints
YOLO_SERVICE_ENDPOINT = os.environ['YOLO_SERVICE_ENDPOINT']
BYTETRACK_SERVICE_ENDPOINT = os.environ['BYTETRACK_SERVICE_ENDPOINT']

# Temporary file paths
TEMP_INPUT_VIDEO = '/tmp/input.mp4'
TEMP_OUTPUT_VIDEO = '/tmp/output.mp4'
TEMP_OUTPUT_JSON = '/tmp/output.json'

# Initialize S3 client
s3_client = boto3.client('s3')


def download_from_s3(bucket_name, source_blob_name, destination_file_name):
    logger.info(
        f"Downloading {source_blob_name} from {bucket_name} to {destination_file_name}"
    )
    s3_client.download_file(bucket_name, source_blob_name,
                            destination_file_name)
    logger.info(f"Download completed: {destination_file_name}")


def upload_to_s3(bucket_name, source_file_name, destination_blob_name):
    logger.info(
        f"Uploading {source_file_name} to {bucket_name}/{destination_blob_name}"
    )
    s3_client.upload_file(source_file_name, bucket_name, destination_blob_name)
    logger.info(f"Upload completed: {bucket_name}/{destination_blob_name}")


def read_metadata():
    with open('/tmp/metadata.json', 'r') as f:
        return json.load(f)


# Taking out annotation to separate job processing (video-annotation batch job)
# def annotate_video(input_video_path, final_results):
#     """
#     Annotate the input video with detection and tracking results.

#     Args:
#         input_video_url (str): URL of the input video
#         final_results (list): List of detection and tracking results

#     Returns:
#         tuple: A message and status code
#     """
#     logger.info(f"Starting video annotation: {input_video_path}")
#     if not os.path.exists(input_video_path):
#         raise Exception(f"Input video file not found: {input_video_path}")

#     # Initialize video capture and writer
#     cap = cv2.VideoCapture(input_video_path)
#     width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
#     height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
#     fps = int(cap.get(cv2.CAP_PROP_FPS))
#     output_video = cv2.VideoWriter(TEMP_OUTPUT_VIDEO,
#                                    cv2.VideoWriter_fourcc(*'mp4v'), fps,
#                                    (width, height))

#     # Group final_results by frame_id for efficient processing
#     results_by_frame = {}
#     for result in final_results:
#         frame_id = result.get('frame_id')
#         if frame_id not in results_by_frame:
#             results_by_frame[frame_id] = []
#         results_by_frame[frame_id].append(result)

#     frame_count = 0
#     while cap.isOpened():
#         ret, frame = cap.read()
#         if not ret:
#             break

#         # Filter results for the current frame
#         frame_results = [
#             result for result in final_results
#             if result.get('frame_id') == frame_count
#         ]

#         # Get the full shape of the frame
#         height, width, channels = frame.shape

#         # Process detection and tracking results for current frame
#         frame_results = results_by_frame.get(frame_count, [])
#         for final_result in frame_results:
#             # Extract and validate result data
#             track_id = final_result.get('track_id')
#             if track_id is None:
#                 continue
#             box = final_result.get('box')
#             confidence = final_result.get('confidence')
#             class_name = final_result.get('class_name')

#             # Check if box is in the new format and not empty
#             if box and isinstance(box, list) and len(box) > 0 and isinstance(
#                     box[0], dict):
#                 # Extract coordinates from the new format
#                 x1 = box[0].get('x1')
#                 y1 = box[0].get('y1')
#                 x2 = box[0].get('x2')
#                 y2 = box[0].get('y2')
#             else:
#                 # Handle case where box is not in the expected format
#                 print(f"Unexpected box format for track_id {track_id}: {box}")
#                 continue

#             # Ensure all coordinates are integers and not None
#             if all(coord is not None for coord in [x1, y1, x2, y2]):
#                 x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
#             else:
#                 print(f"Invalid coordinates for track_id {track_id}: {box}")
#                 continue

#             # Draw bounding boxes and labels on the frame
#             cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
#             label = f"#{track_id} {class_name} {confidence:.2f}"
#             cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX,
#                         0.5, (0, 255, 0), 2)

#         # Write the annotated frame to output video
#         output_video.write(frame)
#         frame_count += 1

#     # Release resources
#     cap.release()
#     output_video.release()

#     logger.info(
#         f"Video annotation completed. Output saved to {TEMP_OUTPUT_VIDEO}")
#     return "Complete annotation", 200


def process_video():
    logger.info(f"Starting to process video: {INPUT_VIDEO}")
    logger.info(
        f"Environment variables: INPUT_BUCKET={INPUT_BUCKET}, OUTPUT_BUCKET={OUTPUT_BUCKET}, REQUEST_ID={REQUEST_ID}"
    )
    logger.info(f"YOLO_SERVICE_ENDPOINT: {YOLO_SERVICE_ENDPOINT}")
    logger.info(f"BYTETRACK_SERVICE_ENDPOINT: {BYTETRACK_SERVICE_ENDPOINT}")
    request_data = {
        "request_id": REQUEST_ID,
        "bucket_name": OUTPUT_BUCKET,
        "object_name": INPUT_VIDEO
    }

    try:
        print(f"Processing video: {INPUT_VIDEO}")

        try:
            # Step 1: Send video to YOLO service for detection
            logger.info("Sending video to YOLO service for detection")
            yolo_response = requests.post(f"{YOLO_SERVICE_ENDPOINT}/detect",
                                          json=request_data)
            yolo_response.raise_for_status()
            detection_results = yolo_response.json()
            logger.info(
                f"YOLO detection completed. Received {len(detection_results)} results."
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Error connecting to YOLO service: {e}",
                         exc_info=True)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error in YOLO service: {e}",
                         exc_info=True)
            sys.exit(1)

        try:
            # Step 2: Send YOLO results to Bytetrack service for tracking
            logger.info(
                "Sending YOLO results to Bytetrack service for tracking")
            bytetrack_response = requests.post(
                f"{BYTETRACK_SERVICE_ENDPOINT}/track", json=detection_results)
            bytetrack_response.raise_for_status()
            final_results = bytetrack_response.json()
            logger.info(
                f"Bytetrack tracking completed. Received {len(final_results)} results."
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Error connecting to Bytetrack service: {e}",
                         exc_info=True)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error in Bytetrack service: {e}",
                         exc_info=True)
            sys.exit(1)

        # Save final results to JSON file
        logger.info(f"Saving final results to {TEMP_OUTPUT_JSON}")
        with open(TEMP_OUTPUT_JSON, 'w') as f:
            json.dump(final_results, f, indent=2)

        # Download input video from S3
        # logger.info(
        #     f"Downloading input video from S3: {OUTPUT_BUCKET}/{INPUT_VIDEO}")
        # download_from_s3(OUTPUT_BUCKET, INPUT_VIDEO, TEMP_INPUT_VIDEO)

        # Taking out annotation to separate job processing (video-annotation batch job)
        # Step 3: Annotate video with final results
        # try:
        #     logger.info("Starting video annotation")
        #     annotate_video(TEMP_INPUT_VIDEO, final_results)
        #     logger.info(
        #         f"Video annotation completed for Request ID #{REQUEST_ID}")
        # except Exception as e:
        #     logger.error(f"Error in annotate_video: {str(e)}", exc_info=True)
        #     sys.exit(1)

        # 4th: Upload outputs to output bucket
        output_video_path = INPUT_VIDEO.replace('split_chunks',
                                                'processed_chunks')
        output_json_path = output_video_path.rsplit('.', 1)[0] + '.json'

        # logger.info(
        #     f"Uploading output video to S3: {OUTPUT_BUCKET}/{output_video_path}"
        # )
        # upload_to_s3(OUTPUT_BUCKET, TEMP_OUTPUT_VIDEO, output_video_path)

        logger.info(
            f"Uploading output json to S3: {OUTPUT_BUCKET}/{output_json_path}")
        upload_to_s3(OUTPUT_BUCKET, TEMP_OUTPUT_JSON, output_json_path)

        return f"Processing complete. Output json stored in output bucket: {OUTPUT_BUCKET}/{output_json_path}/"

    except requests.exceptions.RequestException as e:
        logger.error(f"Error in HTTP request: {str(e)}", exc_info=True)
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON response: {str(e)}", exc_info=True)
        sys.exit(1)
    except boto3.exceptions.Boto3Error as e:
        logger.error(f"Error in S3 operation: {str(e)}", exc_info=True)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error processing video: {str(e)}",
                     exc_info=True)
        sys.exit(1)

    finally:
        # Clean up temporary files
        if os.path.exists(TEMP_INPUT_VIDEO):
            os.remove(TEMP_INPUT_VIDEO)
        if os.path.exists(TEMP_OUTPUT_VIDEO):
            os.remove(TEMP_OUTPUT_VIDEO)
        if os.path.exists(TEMP_OUTPUT_JSON):
            os.remove(TEMP_OUTPUT_JSON)


if __name__ == "__main__":
    try:
        result = process_video()
        logger.info(result)
    except Exception as e:
        logger.error(f"Error in main execution: {str(e)}", exc_info=True)
        sys.exit(1)
