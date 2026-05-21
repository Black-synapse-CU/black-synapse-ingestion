import pyrealsense2 as rs
import numpy as np
import cv2

# Configure pipeline
pipeline = rs.pipeline()
config = rs.config()

# Enable all streams but we will choose which to display
config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
config.enable_stream(rs.stream.infrared, 1, 640, 480, rs.format.y8, 30)  # IR1

# Start the camera
pipeline.start(config)

# Current mode: 'rgb', 'depth', 'infra'
mode = 'rgb'

try:
    while True:
        frames = pipeline.wait_for_frames()

        if mode == 'rgb':
            frame = frames.get_color_frame()
            if not frame:
                continue
            image = np.asanyarray(frame.get_data())

        elif mode == 'depth':
            frame = frames.get_depth_frame()
            if not frame:
                continue
            image = np.asanyarray(frame.get_data())
            # Optional: apply color map for visualization
            image = cv2.applyColorMap(cv2.convertScaleAbs(image, alpha=0.03), cv2.COLORMAP_JET)

        elif mode == 'infra':
            frame = frames.get_infrared_frame(1)  # IR1
            if not frame:
                continue
            image = np.asanyarray(frame.get_data())

        # Show the image
        cv2.imshow("RealSense", image)
        key = cv2.waitKey(1) & 0xFF

        # Switch modes
        if key == ord('r'):
            mode = 'rgb'
            print("Switched to RGB mode")
        elif key == ord('d'):
            mode = 'depth'
            print("Switched to Depth mode")
        elif key == ord('i'):
            mode = 'infra'
            print("Switched to Infrared mode")
        elif key == 27:  # ESC key
            break

finally:
    pipeline.stop()
    cv2.destroyAllWindows()
