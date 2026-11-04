"""Image preprocessing.

Turns a photo of a paper / whiteboard sketch into stark black-on-white line art
so the AI sees the layout and not the shadows, lighting or smudges.
"""

from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps


@dataclass
class VisionSettings:
    max_side: int = 1600          # longest side after resizing, in pixels
    remove_shadows: bool = True   # flatten uneven lighting before thresholding
    block_size: int = 31          # adaptive threshold neighbourhood (odd)
    contrast: int = 12            # adaptive threshold constant C; higher = fewer faint lines
    min_speck_area: int = 30      # ink blobs smaller than this are treated as noise
    auto_crop: bool = True        # trim empty margins around the drawing


def load_image(data: bytes) -> np.ndarray:
    """Decode uploaded bytes into a BGR array, honouring phone EXIF rotation."""
    image = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _resize(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1:
        return image
    return cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)


def _remove_shadows(gray: np.ndarray) -> np.ndarray:
    """Estimate the paper background and divide it out, leaving only the ink."""
    background = cv2.dilate(gray, np.ones((7, 7), np.uint8))
    background = cv2.medianBlur(background, 21)
    return cv2.divide(gray, background, scale=255)


def _remove_specks(binary: np.ndarray, min_area: int) -> np.ndarray:
    """Drop tiny isolated ink blobs (dust, paper grain, sensor noise)."""
    ink = cv2.bitwise_not(binary)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= min_area
    keep[0] = False  # label 0 is the background
    return np.where(keep[labels], 0, 255).astype(np.uint8)


def _crop_to_ink(binary: np.ndarray, padding: int = 24) -> np.ndarray:
    points = cv2.findNonZero(cv2.bitwise_not(binary))
    if points is None:
        return binary
    x, y, w, h = cv2.boundingRect(points)
    height, width = binary.shape
    return binary[max(y - padding, 0):min(y + h + padding, height),
                  max(x - padding, 0):min(x + w + padding, width)]


def clean_sketch(image: np.ndarray, settings: VisionSettings | None = None) -> np.ndarray:
    """Return a single-channel image: black ink lines on a pure white background."""
    settings = settings or VisionSettings()

    gray = cv2.cvtColor(_resize(image, settings.max_side), cv2.COLOR_BGR2GRAY)

    # Chalkboards and dark-mode screenshots: flip so the ink is always the dark part.
    if np.median(gray) < 100:
        gray = cv2.bitwise_not(gray)

    if settings.remove_shadows:
        gray = _remove_shadows(gray)

    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    block_size = max(3, settings.block_size | 1)  # must be odd
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, settings.contrast
    )

    if settings.min_speck_area > 0:
        binary = _remove_specks(binary, settings.min_speck_area)
    if settings.auto_crop:
        binary = _crop_to_ink(binary)
    return binary


def encode(image: np.ndarray, extension: str = ".png") -> bytes:
    ok, buffer = cv2.imencode(extension, image)
    if not ok:
        raise ValueError(f"Could not encode the image as {extension}")
    return buffer.tobytes()


def process_upload(data: bytes, settings: VisionSettings | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Returns (original BGR, resized; cleaned grayscale)."""
    settings = settings or VisionSettings()
    original = _resize(load_image(data), settings.max_side)
    return original, clean_sketch(original, settings)


def looks_like_sketch(image: np.ndarray) -> bool:
    """Photos of pen-on-paper are nearly colourless; screenshots and mockups usually aren't."""
    saturation = cv2.cvtColor(_resize(image, 600), cv2.COLOR_BGR2HSV)[:, :, 1]
    return float(np.percentile(saturation, 95)) < 60


def classify(data: bytes) -> str:
    return "sketch" if looks_like_sketch(load_image(data)) else "screenshot"


def prepare(data: bytes, kind: str) -> dict:
    """What gets sent to the AI: the (resized) photo, plus a cleaned line drawing for sketches."""
    original = _resize(load_image(data), VisionSettings().max_side)
    return {
        "original": encode(original, ".jpg"),
        "cleaned": encode(clean_sketch(original)) if kind == "sketch" else None,
    }


def normalize_upload(data: bytes, max_side: int = 2000) -> bytes:
    """Uploads are stored in the database as a JPEG no larger than 2000px, which keeps
    rows small (a phone photo drops from ~5 MB to ~400 KB) without losing detail."""
    image = _resize(load_image(data), max_side)
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("Could not encode the image")
    return buffer.tobytes()
