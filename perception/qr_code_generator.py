import qrcode
import json
import os

# Example location map
location_map = {
    "LOC_001": "Main entrance",
    "LOC_002": "Hallway near elevators",
    "LOC_003": "Charging station",
    "LOC_004": "Whiteboard area"
}

os.makedirs("qr_codes", exist_ok=True)

for loc_id in location_map.keys():
    qr = qrcode.make(loc_id)
    qr.save(f"qr_codes/{loc_id}.png")

# Save descriptions too
with open("locations.json", "w") as f:
    json.dump(location_map, f, indent=4)

print("QR codes + JSON file created!")