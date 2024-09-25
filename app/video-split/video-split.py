# Video split job - with AWS
import os
import sys
import json
import subprocess
import boto3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize AWS S3 client
s3_client = boto3.client('s3')

# output bucket
TASK_TOKEN = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('TASK_TOKEN')
INPUT_BUCKET = sys.argv[2] if len(
    sys.argv) > 2 else os.environ.get('INPUT_BUCKET')
INPUT_VIDEO = sys.argv[3] if len(
    sys.argv) > 3 else os.environ.get('INPUT_VIDEO')
REQUEST_ID = sys.argv[4] if len(sys.argv) > 4 else os.environ.get('REQUEST_ID')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET')
SEGMENT_DURATION = os.environ.get('SEGMENT_DURATION', 3)


def split_video():
    try:
        logging.info("Starting video splitting process")
        # Your existing code here
        input_bucket_name = INPUT_BUCKET
        input_object_name = INPUT_VIDEO

        logging.info(
            f"Input Bucket: {input_bucket_name}, Input Video: {input_object_name}, Output Bucket: {OUTPUT_BUCKET}"
        )

        if not input_bucket_name or not input_object_name or not OUTPUT_BUCKET:
            logging.error(
                'Invalid input: Missing INPUT_BUCKET, INPUT_VIDEO, or OUTPUT_BUCKET environment variables'
            )
            return json.dumps(
                {'error': 'Missing required environment variables'}), 400

        if not input_object_name.lower().endswith(
            ('.mp4', '.avi', '.mov')):  # Add other video formats as needed
            logging.error(
                f"Invalid input video file name: {input_object_name}")
            return json.dumps({'error': 'Invalid input video file name'}), 400

        input_video_path = f"/tmp/{input_object_name}"
        output_dir = f"/tmp/{REQUEST_ID}/"

        try:
            # Download input video
            logging.info(
                f"Downloading input video from {input_bucket_name}/{input_object_name}"
            )
            s3_client.download_file(input_bucket_name, input_object_name,
                                    input_video_path)
            logging.info("Input video downloaded successfully")

            # Set up output directory
            os.makedirs(output_dir, exist_ok=True)
            logging.info(f"Created output directory: {output_dir}")

            # Split video
            output_pattern = os.path.join(output_dir, "output%04d.mp4")
            segment_duration = SEGMENT_DURATION  # You can make this configurable if needed

            ffmpeg_command = [
                'ffmpeg', '-i', input_video_path, '-c:v', 'libx264', '-crf',
                '22', '-map', '0', '-segment_time',
                str(segment_duration), '-reset_timestamps', '1', '-g', '50',
                '-sc_threshold', '0', '-force_key_frames',
                f'expr:gte(t,n_forced*{segment_duration})', '-f', 'segment',
                output_pattern
            ]

            logging.info(
                f"Executing ffmpeg command: {' '.join(ffmpeg_command)}")
            subprocess.run(ffmpeg_command, check=True)
            logging.info(
                f"Video successfully split into segments in {output_dir}")

            # Generate metadata and upload segments
            segment_files = sorted(
                [f for f in os.listdir(output_dir) if f.endswith(".mp4")])
            logging.info(f"Found {len(segment_files)} segment files")

            manifest = []
            for i, segment_file in enumerate(segment_files):
                segment_path = os.path.join(output_dir, segment_file)
                metadata = {
                    "request_id": REQUEST_ID,
                    "segment_file": segment_file,
                    "segment_number": i,
                    "start_time": i * segment_duration,
                    "duration": segment_duration,
                    "original_video": input_object_name
                }

                # Upload segment video
                s3_client.upload_file(
                    segment_path, OUTPUT_BUCKET,
                    f"{REQUEST_ID}/split_chunks/{segment_file}")
                logging.info(
                    f"Uploaded segment {i+1}/{len(segment_files)}: {segment_file}"
                )

                # Generate and upload metadata JSON
                json_filename = os.path.splitext(segment_file)[0] + '.json'
                json_path = os.path.join(output_dir, json_filename)
                with open(json_path, 'w') as f:
                    json.dump(metadata, f, indent=2)

                s3_client.upload_file(
                    json_path, OUTPUT_BUCKET,
                    f"{REQUEST_ID}/split_chunks/{json_filename}")
                logging.info(
                    f"Uploaded metadata for segment {i+1}/{len(segment_files)}: {json_filename}"
                )

                manifest.append(metadata)

            # Add segment_count to manifest
            manifest_with_count = {
                "segment_count": len(segment_files),
                "segments": manifest
            }

            # Upload manifest
            manifest_path = os.path.join(output_dir, "manifest.json")
            with open(manifest_path, 'w') as f:
                json.dump(manifest_with_count, f, indent=2)

            s3_client.upload_file(manifest_path, OUTPUT_BUCKET,
                                  f"{REQUEST_ID}/manifest.json")
            logging.info("Uploaded manifest.json")

            # return json.dumps({
            #     'message':
            #     'Video splitting completed successfully',
            #     'request_id':
            #     REQUEST_ID,
            #     'segment_count':
            #     len(segment_files),
            #     'segments': [{
            #         "segment_file": segment_file,
            #         "segment_number": i
            #     } for i, segment_file in enumerate(segment_files)]
            # }), 200

            # We can't do waitForTaskToken with submitJob
            # To allow step function to get the output result directly as response,
            # we need to use the callback pattern by passing the task token with `send_task_success`.
            result = {
                'message':
                'Video splitting completed successfully',
                'request_id':
                REQUEST_ID,
                'segment_count':
                len(segment_files),
                'segments': [{
                    "segment_file": segment_file,
                    "segment_number": i
                } for i, segment_file in enumerate(segment_files)]
            }

            # Send the result back to Step Functions
            if TASK_TOKEN:
                sfn_client = boto3.client('stepfunctions')
                sfn_client.send_task_success(taskToken=TASK_TOKEN,
                                             output=json.dumps(result))

            return json.dumps(result), 200

        except subprocess.CalledProcessError as e:
            logging.error(f"Error splitting video: {e.stderr}")
            return json.dumps({'error':
                               f'Error splitting video: {e.stderr}'}), 500
        except Exception as e:
            logging.error(f"Error processing video: {str(e)}", exc_info=True)
            return json.dumps({'error':
                               f'Error processing video: {str(e)}'}), 500
        finally:
            # Clean up temporary files
            if os.path.exists(input_video_path):
                os.remove(input_video_path)
                logging.info(
                    f"Removed temporary input file: {input_video_path}")
            if os.path.exists(output_dir):
                for file in os.listdir(output_dir):
                    os.remove(os.path.join(output_dir, file))
                os.rmdir(output_dir)
                logging.info(
                    f"Removed temporary output directory: {output_dir}")

            logging.info("Video splitting completed successfully")
    except Exception as e:
        logging.error(f"Unhandled exception in split_video: {str(e)}",
                      exc_info=True)
        return json.dumps({'error': f'Unhandled exception: {str(e)}'}), 500


if __name__ == "__main__":
    try:
        result, status_code = split_video()
        print(result)
        exit(0 if status_code == 200 else 1)
    except Exception as e:
        logging.error(f"Unhandled exception in main: {str(e)}", exc_info=True)
        exit(1)
