import flask
from yolox.tracker.byte_tracker import BYTETracker
from yolox.tracker.basetrack import BaseTrack
import numpy as np
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    stream=sys.stdout)
logger = logging.getLogger(__name__)


# Define BYTETrackerArgs class with default parameters
class BYTETrackerArgs:
    track_thresh: float = 0.5
    track_buffer: int = 50
    match_thresh: float = 0.8
    mot20: bool = False
    aspect_ratio_thresh = 10.0
    min_box_area = 1.0


app = flask.Flask(__name__)


# For healthcheck
@app.route('/')
def home():
    return "ByteTrack service is running", 200


@app.route('/reset_ids', methods=['POST'])
def reset_ids():
    try:
        # Log the current ID before reset
        current_id = BaseTrack._count
        logger.info(f"Current ID before reset: {current_id}")
        sys.stdout.flush()

        BaseTrack.reset_ids()

        # Verify the reset
        new_id = BaseTrack._count
        logger.info(f"ID after reset: {new_id}")
        sys.stdout.flush()

        if new_id != 0:
            logger.error(f"Reset failed. ID is {new_id} instead of 0")
            sys.stdout.flush()  # Force flush the output
            return flask.jsonify({'error': 'Reset failed'}), 500

        logger.info("Reset successful")
        sys.stdout.flush()

        return flask.jsonify({'message':
                              'Tracking IDs reset successfully'}), 200
    except Exception as e:
        logger.error(f"Failed to reset tracking IDs: {str(e)}")
        sys.stdout.flush()
        return flask.jsonify(
            {'error': f'Failed to reset tracking IDs: {str(e)}'}), 500


# Main tracking processing endpoint
@app.route('/track', methods=['POST'])
def track():
    try:
        # Get request's JSON data from main tracking-service
        detection_results = flask.request.get_json()
        tracking_results = []

        if not detection_results:
            return flask.jsonify({'error': 'No detections provided'}), 400

        current_id = BaseTrack._count
        logger.info(f"Current Byetracker tracker ID: {current_id} from")
        sys.stdout.flush()

        # Initialize Bytetrack instance
        tracker = BYTETracker(BYTETrackerArgs())

        # Process each frame in detection results
        for frame_result in detection_results:
            try:
                # Extract each frame's info
                request_id = frame_result.get('request_id')
                frame_id = frame_result.get('frame_id')
                timestamp = frame_result.get('timestamp')
                boxes = frame_result.get('box')
                scores = frame_result.get('confidence')
                class_id = frame_result.get('class_id')
                class_name = frame_result.get('class_name')
                shape_str = frame_result.get('shape')
                # Process shape information required for Bytetrack input
                try:
                    height, width, channels = map(int, shape_str.split(','))
                    img_info = (height, width, channels)
                except ValueError:
                    print(f"Invalid shape information for frame {frame_id}.")
                    raise ValueError(
                        f"Invalid shape information for frame {frame_id}.")

                # Frames with missing required data: Skip ByteTrack processing
                # but keep each frame for record with null info
                if any(
                        x in [None, ''] for x in
                    [frame_id, boxes, scores, class_id, class_name, shape_str
                     ]):
                    tracking_results.append({
                        "request_id":
                        request_id,
                        'frame_id':
                        frame_id,
                        'timestamp':
                        timestamp,
                        'track_id':
                        None,
                        'box': [{
                            'x1': None,
                            'y1': None,
                            'x2': None,
                            'y2': None
                        }],
                        'confidence':
                        getattr(frame_result, 'confidence', None),
                        'class_id':
                        getattr(frame_result, 'class_id', None),
                        'class_name':
                        getattr(frame_result, 'class_name', None)
                    })
                    print(f"Missing required data: Skip ByteTrack processing\
                            & append empty result for frame {frame_id}")
                    continue

                # Skip ByteTrack processing for frames with no detections
                # but keep each frame for record with null info
                if len(boxes) == 0:
                    tracking_results.append({
                        "request_id":
                        request_id,
                        'frame_id':
                        frame_id,
                        'timestamp':
                        timestamp,
                        'track_id':
                        None,
                        'box': [{
                            'x1': None,
                            'y1': None,
                            'x2': None,
                            'y2': None
                        }],
                        'confidence':
                        getattr(frame_result, 'confidence', None),
                        'class_id':
                        getattr(frame_result, 'class_id', None),
                        'class_name':
                        getattr(frame_result, 'class_name', None)
                    })
                    print(f"No YOLO detections: Skip ByteTrack processing\
                            & append empty result for frame {frame_id}")
                    continue

                # Prepare input for ByteTrack
                try:
                    bytetrack_input = []
                    for box, score in zip(boxes, scores):
                        x1, y1, x2, y2 = box
                        bytetrack_input.append((x1, y1, x2, y2, score))
                    # Convert to numpy array
                    bytetrack_input = np.array(bytetrack_input)
                    # print(f"processing bytetrack input for frame {frame_id}****: {bytetrack_input}")
                except Exception as e:
                    raise ValueError(f"Error preparing ByteTrack input\
                                     for frame {frame_id}: {str(e)}")

                # Run ByteTrack processing
                try:
                    online_targets = tracker.update(bytetrack_input, img_info,
                                                    img_info)
                    # Skip processing if Bytetrack return empty result
                    # but keep each frame for record with null info
                    if not online_targets:
                        print(f"No online targets returned from Bytetrack:\
                                Append empty result for frame {frame_id}")
                        tracking_results.append({
                            "request_id":
                            request_id,
                            'frame_id':
                            frame_id,
                            'timestamp':
                            timestamp,
                            'track_id':
                            None,
                            'box': [{
                                'x1': None,
                                'y1': None,
                                'x2': None,
                                'y2': None
                            }],
                            'confidence':
                            None,
                            'class_id':
                            None,
                            'class_name':
                            None
                        })
                        continue
                except Exception as e:
                    raise RuntimeError(
                        f"ByteTrack update failed for frame {frame_id},\
                            {bytetrack_input}: {str(e)}")

                # Process tracking results. Reference:
                # https://github.com/ifzhang/ByteTrack/blob/d1bf0191adff59bc8fcfeaa0b33d3d1642552a99/tools/demo_track.py#L188
                online_tlwhs = []
                online_ids = []
                online_scores = []
                i = 0
                for t in online_targets:
                    tlwh = t.tlwh
                    tid = getattr(t, 'track_id', None)
                    vertical = tlwh[2] / tlwh[
                        3] > BYTETrackerArgs.aspect_ratio_thresh
                    if tlwh[2] * tlwh[
                            3] > BYTETrackerArgs.min_box_area and not vertical:
                        online_tlwhs.append(tlwh)
                        online_ids.append(tid)
                        online_scores.append(t.score)
                        x1, y1, w, h = tlwh
                        box = tuple(map(int, (x1, y1, x1 + w, y1 + h)))
                        result_dict = {
                            "request_id":
                            request_id,
                            'frame_id':
                            frame_id,
                            'timestamp':
                            timestamp,
                            'track_id':
                            tid,
                            'box': [{
                                'x1': box[0],
                                'y1': box[1],
                                'x2': box[2],
                                'y2': box[3]
                            }],
                            'confidence':
                            round(float(t.score), 2),
                            'class_id':
                            int(class_id[i]),
                            'class_name':
                            class_name[i]
                        }
                        i += 1
                        tracking_results.append(result_dict)
                    else:
                        print(f"Filtered detections:\
                              Append empty result for frame {frame_id}")
            except RuntimeError as e:
                return flask.jsonify(
                    {'error': f"ByteTrack processing error: {str(e)}"}), 500
            except Exception as e:
                return flask.jsonify({
                    'error':
                    f"Unexpected error processing frame {frame_id}: {str(e)}"
                }), 500

        return flask.jsonify(tracking_results)
    except Exception as e:
        return flask.jsonify(
            {'error': f"Unexpected error in ByteTrack service: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
