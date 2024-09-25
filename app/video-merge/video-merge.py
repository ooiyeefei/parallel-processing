import os
import json
import subprocess
import boto3
import logging
import tempfile
import shutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
REQUEST_ID = os.environ.get('REQUEST_ID')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET')

s3_client = boto3.client('s3')


def download_from_s3(bucket_name, source_blob_name, destination_file_name):
    try:
        logger.info(
            f"Downloading {source_blob_name} from {bucket_name} to {destination_file_name}"
        )
        s3_client.download_file(bucket_name, source_blob_name,
                                destination_file_name)
        logger.info(f"Download completed: {destination_file_name}")
    except Exception as e:
        logger.error(
            f"Error downloading {source_blob_name} from {bucket_name}: {str(e)}"
        )
        raise


def upload_to_s3(bucket_name, source_file_name, destination_blob_name):
    try:
        logger.info(
            f"Uploading {source_file_name} to {bucket_name}/{destination_blob_name}"
        )
        s3_client.upload_file(source_file_name, bucket_name,
                              destination_blob_name)
        logger.info(f"Upload completed: {bucket_name}/{destination_blob_name}")
    except Exception as e:
        logger.error(
            f"Error uploading {source_file_name} to {bucket_name}/{destination_blob_name}: {str(e)}"
        )
        raise


def create_videolist(manifest_data, temp_dir):
    try:
        videolist_path = os.path.join(temp_dir, 'videolist.txt')

        # Extract 'segments' from the manifest
        segments = manifest_data.get('segments', [])

        video_files = [
            segment['segment_file'] for segment in segments
            if segment['segment_file'].endswith('.mp4')
        ]
        video_files.sort()  # Ensure correct order

        with open(videolist_path, 'w') as f:
            for video_file in video_files:
                f.write(f"file '{video_file}'\n")

        logger.info(f"Created videolist at {videolist_path}")
        return videolist_path
    except Exception as e:
        logger.error(f"Error creating videolist: {str(e)}")
        raise


def merge_videos():
    logger.info(f"Starting video merge process for request ID: {REQUEST_ID}")
    temp_dir = tempfile.mkdtemp()

    try:
        # Download and read manifest.json
        manifest_path = os.path.join(temp_dir, 'manifest.json')
        download_from_s3(OUTPUT_BUCKET, f"{REQUEST_ID}/manifest.json",
                         manifest_path)
        with open(manifest_path, 'r') as f:
            manifest_data = json.load(f)

        # Download video chunks
        segments = manifest_data.get('segments', [])
        for segment in segments:
            if segment['segment_file'].endswith('.mp4'):
                source_path = f"{REQUEST_ID}/processed_chunks/{segment['segment_file']}"
                dest_path = os.path.join(temp_dir, segment['segment_file'])
                download_from_s3(OUTPUT_BUCKET, source_path, dest_path)
                if not os.path.exists(dest_path):
                    raise FileNotFoundError(
                        f"Input file not found: {dest_path}")

        # Create videolist.txt
        videolist_path = create_videolist(manifest_data, temp_dir)

        # Merge videos
        output_path = os.path.join(temp_dir, f'merged_{REQUEST_ID}.mp4')
        ffmpeg_command = [
            'ffmpeg', '-f', 'concat', '-safe', '0', '-i', videolist_path,
            '-c:v', 'libx264', '-c:a', 'copy', '-avoid_negative_ts',
            'make_zero', '-movflags', '+faststart', output_path
        ]

        result = subprocess.run(ffmpeg_command,
                                check=True,
                                capture_output=True,
                                text=True)
        logger.info("Video merge completed successfully")
        logger.debug(f"FFmpeg stdout: {result.stdout}")
        logger.debug(f"FFmpeg stderr: {result.stderr}")

        # Upload merged video to GCS
        upload_to_s3(OUTPUT_BUCKET, output_path,
                     f"{REQUEST_ID}/merged_video.mp4")
        logger.info(
            f"Merged video uploaded to {OUTPUT_BUCKET}/{REQUEST_ID}/merged_video.mp4"
        )

    except subprocess.CalledProcessError as e:
        logger.error(f"Error during video merge: {str(e)}")
        logger.error(f"FFmpeg stdout: {e.stdout}")
        logger.error(f"FFmpeg stderr: {e.stderr}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during video merge process: {str(e)}")
        raise
    finally:
        # Clean up temporary files
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info("Temporary files cleaned up")


if __name__ == "__main__":
    try:
        merge_videos()
    except Exception as e:
        logger.error(f"Video merge process failed: {str(e)}")
        exit(1)
