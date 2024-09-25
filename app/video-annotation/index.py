import json
import boto3
import cv2
import os
import logging
from collections import defaultdict

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

s3 = boto3.client('s3')

REQUEST_ID = os.environ['REQUEST_ID']
INPUT_BUCKET = os.environ['INPUT_BUCKET']
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET']
INPUT_VIDEO = os.environ['INPUT_VIDEO']


def adjust_frame_and_timestamp(results, start_frame, start_time):
    for result in results:
        result['frame_id'] += start_frame
        result['timestamp'] += start_time
    return results


def reassign_track_ids(all_results):
    track_id_map = {}
    new_track_id = 1

    # Sort all results by frame_id and timestamp
    all_results.sort(key=lambda x: (x['frame_id'], x['timestamp']))

    for result in all_results:
        original_track_id = result['track_id']
        if original_track_id not in track_id_map:
            track_id_map[original_track_id] = new_track_id
            new_track_id += 1
        result['track_id'] = track_id_map[original_track_id]

    return all_results


def process_json_files(manifest_data):
    # Download and merge all JSON files listed in the manifest
    all_results = []
    total_segments = len(manifest_data.get('segments', []))
    logger.info(f"Total segments in manifest: {total_segments}")

    start_frame = 0
    start_time = 0.0

    for segment in manifest_data.get('segments', []):
        json_file = segment['segment_file'].replace('.mp4', '.json')
        logger.info(f"Processing JSON file: {json_file}")

        try:
            json_obj = s3.get_object(
                Bucket=OUTPUT_BUCKET,
                Key=f"{REQUEST_ID}/processed_chunks/{json_file}")
            segment_data = json.loads(json_obj['Body'].read().decode('utf-8'))
            logger.info(f"Segment data length: {len(segment_data)}")

            # Adjust frame_id and timestamp for this segment
            adjusted_segment_data = adjust_frame_and_timestamp(
                segment_data, start_frame, start_time)
            all_results.extend(adjusted_segment_data)

            # Update start_frame and start_time for the next segment
            if segment_data:
                last_result = segment_data[-1]
                start_frame = last_result['frame_id'] + 1
                start_time = last_result[
                    'timestamp'] + 0.04  # Assuming 25 fps (1/25 = 0.04)

        except Exception as e:
            logger.error(f"Error processing JSON file {json_file}: {str(e)}")

    return all_results


def annotate_video(results_by_frame, input_path, output_path):
    # Annotate video
    logger.info("Starting video annotation")
    cap = cv2.VideoCapture('/tmp/input.mp4')
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter('/tmp/output.mp4', fourcc, cap.get(cv2.CAP_PROP_FPS),
                          (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                           int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))))

    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Process detection and tracking results for current frame
        frame_results = results_by_frame.get(frame_count, [])
        for final_result in frame_results:
            # Extract and validate result data
            track_id = final_result.get('track_id')
            if track_id is None:
                continue
            box = final_result.get('box')
            confidence = final_result.get('confidence')
            class_name = final_result.get('class_name')

            # Check if box is in the new format and not empty
            if box and isinstance(box, list) and len(box) > 0 and isinstance(
                    box[0], dict):
                # Extract coordinates from the new format
                x1 = box[0].get('x1')
                y1 = box[0].get('y1')
                x2 = box[0].get('x2')
                y2 = box[0].get('y2')
            else:
                # Handle case where box is not in the expected format
                logger.warning(
                    f"Unexpected box format for track_id {track_id}: {box}")
                continue

            # Ensure all coordinates are integers and not None
            if all(coord is not None for coord in [x1, y1, x2, y2]):
                x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
            else:
                logger.warning(
                    f"Invalid coordinates for track_id {track_id}: {box}")
                continue

            # Draw bounding boxes and labels on the frame
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"#{track_id} {class_name} {confidence:.2f}"
            cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 255, 0), 2)

        out.write(frame)
        frame_count += 1

        if frame_count % 100 == 0:
            logger.info(f"Processed {frame_count} frames")

    cap.release()
    out.release()
    logger.info(
        f"Video annotation completed. Total frames processed: {frame_count}")


def handler(event, context):
    try:
        logger.info(f"Starting video annotation for REQUEST_ID: {REQUEST_ID}")
        logger.info(f"Input video: {INPUT_VIDEO} from bucket: {INPUT_BUCKET}")

        # Download original video
        logger.info("Downloading original video")
        s3.download_file(INPUT_BUCKET, INPUT_VIDEO, '/tmp/input.mp4')
        logger.info("Original video downloaded successfully")

        # Download and read manifest.json
        logger.info("Downloading manifest.json")
        manifest_obj = s3.get_object(Bucket=OUTPUT_BUCKET,
                                     Key=f"{REQUEST_ID}/manifest.json")
        manifest_data = json.loads(manifest_obj['Body'].read().decode('utf-8'))
        logger.info(f"Manifest data: {json.dumps(manifest_data)}")

        # Process JSON files
        all_results = process_json_files(manifest_data)

        # Reassign track IDs
        all_results = reassign_track_ids(all_results)

        # Upload the re-processed and merged result file to S3
        logger.info("Saving and uploading final results")
        final_results_json = json.dumps(all_results, indent=2)

        with open('/tmp/final_results.json', 'w') as f:
            f.write(final_results_json)

        try:
            s3.upload_file('/tmp/final_results.json', OUTPUT_BUCKET,
                           f"{REQUEST_ID}/final_results.json")
            logger.info("Final results JSON uploaded successfully")
        except Exception as e:
            logger.error(f"Error uploading final results JSON: {str(e)}")

        # Group results by frame_id
        results_by_frame = defaultdict(list)
        for result in all_results:
            results_by_frame[result['frame_id']].append(result)

        # Annotate video
        annotate_video(results_by_frame, '/tmp/input.mp4', '/tmp/output.mp4')

        # Upload annotated video
        logger.info("Uploading annotated video")
        s3.upload_file('/tmp/output.mp4', OUTPUT_BUCKET,
                       f"{REQUEST_ID}/annotated_video.mp4")
        logger.info("Annotated video uploaded successfully")

        return {
            'statusCode': 200,
            'body': json.dumps('Video annotation completed successfully')
        }
    except Exception as e:
        logger.error(f"An error occurred during video annotation: {str(e)}")
        logger.exception("Exception traceback:")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error during video annotation: {str(e)}')
        }


if __name__ == "__main__":
    # For local testing
    handler(None, None)
