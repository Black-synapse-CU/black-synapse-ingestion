import cv2
import argparse
from pathlib import Path


def draw_qr_polygon(image, pts, label):
    """
    Draw QR polygon and label.
    pts should end up as shape (4, 2).
    """
    pts = pts.reshape(-1, 2).astype(int)

    for j in range(len(pts)):
        pt1 = tuple(pts[j])
        pt2 = tuple(pts[(j + 1) % len(pts)])
        cv2.line(image, pt1, pt2, (0, 255, 0), 2)

    x, y = pts[0]
    cv2.putText(
        image,
        label,
        (x, max(20, y - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )


def detect_qr_in_image(image_path: str) -> None:
    image_path = Path(image_path)
    if not image_path.exists():
        print(f"Error: image not found: {image_path}")
        return

    img = cv2.imread(str(image_path))
    if img is None:
        print(f"Error: failed to load image: {image_path}")
        return

    detector = cv2.QRCodeDetector()

    ok, decoded_info, points, _ = detector.detectAndDecodeMulti(img)

    if ok and points is not None:
        for i, qr_text in enumerate(decoded_info):
            pts = points[i]
            label = qr_text if qr_text else "QR detected but not decoded"
            print(f"[IMAGE] QR {i + 1}: {label}")
            draw_qr_polygon(img, pts, label)
    else:
        qr_text, pts, _ = detector.detectAndDecode(img)
        if pts is not None and len(pts) > 0:
            label = qr_text if qr_text else "QR detected but not decoded"
            print(f"[IMAGE] {label}")
            draw_qr_polygon(img, pts, label)
        else:
            print("[IMAGE] No QR code found.")

    cv2.imshow("QR Detection - Image", img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def detect_qr_from_camera(camera_index: int = 0) -> None:
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"Error: could not open camera {camera_index}")
        return

    detector = cv2.QRCodeDetector()
    print("Starting camera QR detection. Press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Error: failed to read frame from camera.")
            break

        ok, decoded_info, points, _ = detector.detectAndDecodeMulti(frame)

        if ok and points is not None:
            for i, qr_text in enumerate(decoded_info):
                pts = points[i]
                label = qr_text if qr_text else "QR detected but not decoded"
                print(f"[CAMERA] QR {i + 1}: {label}")
                draw_qr_polygon(frame, pts, label)
        else:
            qr_text, pts, _ = detector.detectAndDecode(frame)
            if pts is not None and len(pts) > 0:
                label = qr_text if qr_text else "QR detected but not decoded"
                print(f"[CAMERA] {label}")
                draw_qr_polygon(frame, pts, label)

        cv2.imshow("QR Detection - Camera", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="Detect QR codes from image or camera.")
    parser.add_argument(
        "--mode",
        choices=["image", "camera"],
        required=True,
        help="Choose detection mode: image or camera",
    )
    parser.add_argument(
        "--image",
        type=str,
        help="Path to image file when using --mode image",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="Camera index for webcam or USB camera",
    )

    args = parser.parse_args()

    if args.mode == "image":
        if not args.image:
            print("Error: --image is required when --mode image")
            return
        detect_qr_in_image(args.image)
    else:
        detect_qr_from_camera(args.camera_index)


if __name__ == "__main__":
    main()