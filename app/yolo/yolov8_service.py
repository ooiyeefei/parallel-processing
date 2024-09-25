# Yolo service job - with AWS
import flask
import ultralytics
import cv2
import os
import boto3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = flask.Flask(__name__)
model = ultralytics.YOLO('yolov8n.pt')
THRESHOLD = '0.5'

s3_client = boto3.client('s3')


@app.route('/')
def home():
    return "YOLOv8 service is running", 200


@app.route('/detect', methods=['POST'])
def detect():
    try:
        logger.info("Request received")
        try:
            # Parse request data
            request_data = flask.request.get_json()
            logger.info("request data: {request_data}")
            print(f"this is request_data: {request_data}")

            if not request_data or 'bucket_name' not in request_data or 'object_name' not in request_data:
                return flask.jsonify({
                    'error':
                    'No bucket or object name provided in YOLO service'
                }), 400

            bucket_name = request_data.get('bucket_name')
            object_name = request_data.get('object_name')
            request_id = request_data.get('request_id')
            temp_input_video = '/tmp/input.mp4'

            # Download video from bucket
            try:
                logging.info(
                    f"Attempting to download from bucket: {bucket_name}, object: {object_name}"
                )
                s3_client.download_file(bucket_name, object_name,
                                        temp_input_video)
                if not os.path.exists(temp_input_video):
                    logger.error(
                        f"Failed to download video from S3: {bucket_name}/{object_name}"
                    )
                    return flask.jsonify(
                        {'error': 'Failed to download video from S3'}), 500
            except boto3.exceptions.S3TransferFailedError as e:
                logger.error(f"S3 transfer failed: {str(e)}")
                return flask.jsonify(
                    {'error':
                     f'Failed to download video from S3: {str(e)}'}), 500
            except Exception as e:
                logger.error(f"Unexpected error downloading from S3: {str(e)}",
                             exc_info=True)
                return flask.jsonify({
                    'error':
                    f'Unexpected error downloading from S3: {str(e)}'
                }), 500

            try:
                # Process video
                cap = cv2.VideoCapture(temp_input_video)

                # Get video properties
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = int(cap.get(cv2.CAP_PROP_FPS))

                # Initialize list to store detection results
                detection_results = []

                frame_count = 0
                while cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        break

                    # Get the full shape of the frame
                    height, width, channels = frame.shape

                    # Model inference
                    try:
                        detection_results_frame = model.predict(
                            source=frame, conf=float(THRESHOLD), task='detect')
                        # print(f"det_results****: {detection_results_frame}")
                    except Exception as e:
                        return flask.jsonify(
                            {'error':
                             f'Model inference failed: {str(e)}'}), 500

                    # Process results
                    try:
                        boxes = detection_results_frame[0].boxes.xyxy.tolist()
                        confidences = detection_results_frame[
                            0].boxes.conf.tolist()
                        class_ids = detection_results_frame[
                            0].boxes.cls.tolist()
                        class_names = [
                            detection_results_frame[0].names[int(id)]
                            for id in class_ids
                        ]

                        # Append detection results for current frame
                        detection_results.append({
                            "request_id": request_id,
                            "frame_id": frame_count,
                            "timestamp": frame_count / fps,
                            'shape': f"{height},{width},{channels}",
                            'box': boxes,
                            'confidence': confidences,
                            'class_id': class_ids,
                            'class_name': class_names
                        })
                    except Exception as e:
                        return flask.jsonify({
                            'error':
                            f'Error processing detection results: {str(e)}'
                        }), 500

                    frame_count += 1

                cap.release()
            except Exception as e:
                return flask.jsonify(
                    {'error': f'Error processing video: {str(e)}'}), 500
            finally:
                # Clean up temporary files
                if os.path.exists(temp_input_video):
                    os.remove(temp_input_video)

            return flask.jsonify(detection_results)

        except Exception as e:
            return flask.jsonify(
                {'error': f'Unexpected error in YOLO service: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Unexpected error in YOLO service: {str(e)}",
                     exc_info=True)
        return flask.jsonify(
            {'error': f'Unexpected error in YOLO service: {str(e)}'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
