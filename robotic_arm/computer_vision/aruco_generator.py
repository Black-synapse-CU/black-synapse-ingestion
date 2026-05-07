import cv2
import os

# Create output folder
os.makedirs("aruco_markers", exist_ok=True)

# Select dictionary
aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)

# Marker size in pixels (increase for higher quality)
marker_size = 600

# Location markers (IDs 0-3)
location_markers = {
    0: "loc_0",
    1: "loc_1",
    2: "loc_2",
    3: "loc_3",
}

# Object markers (IDs 4+)
object_markers = {
    4: "cube_1",
    5: "cube_2",
    6: "bin_1",
}

all_markers = {**location_markers, **object_markers}

for marker_id, name in all_markers.items():
    marker = cv2.aruco.generateImageMarker(aruco_dict, marker_id, marker_size)
    filename = f"aruco_markers/marker_{name}_id{marker_id}.png"
    cv2.imwrite(filename, marker)
    print(f"Saved {filename}")