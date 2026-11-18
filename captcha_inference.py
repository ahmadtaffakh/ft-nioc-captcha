"""Lightweight CAPTCHA inference without TensorFlow or NumPy."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import sys
import zipfile
from array import array
from pathlib import Path


MODEL_FILENAME = "captcha_model.ftmodel"
MODEL_FORMAT_VERSION = 1
MODEL_WIDTH = 160
MODEL_HEIGHT = 50
MODEL_CHANNELS = 3
MODEL_CLASSES = 10


def write_model_file(
    path: Path,
    metadata: dict,
    kernel_bytes: bytes,
    bias_bytes: bytes,
) -> Path:
    """Atomically write the small, inference-only model artifact."""
    path = Path(path)
    sequence_length = int(metadata["sequence_length"])
    if sequence_length < 1 or MODEL_WIDTH % sequence_length:
        raise ValueError("طول خروجی مدل با عرض تصویر سازگار نیست.")
    features = MODEL_HEIGHT * (MODEL_WIDTH // sequence_length) * MODEL_CHANNELS
    expected_kernel_bytes = features * MODEL_CLASSES * 4
    expected_bias_bytes = MODEL_CLASSES * 4
    if len(kernel_bytes) != expected_kernel_bytes or len(bias_bytes) != expected_bias_bytes:
        raise ValueError("ابعاد وزن‌های مدل سبک معتبر نیست.")

    weights = kernel_bytes + bias_bytes
    manifest = {
        "format_version": MODEL_FORMAT_VERSION,
        "sequence_length": sequence_length,
        "width": MODEL_WIDTH,
        "height": MODEL_HEIGHT,
        "channels": MODEL_CHANNELS,
        "classes": MODEL_CLASSES,
        "features_per_digit": features,
        "weights_sha256": hashlib.sha256(weights).hexdigest(),
        "validation_samples": int(metadata.get("validation_samples", 0)),
        "exact_match": float(metadata.get("exact_match", 0.0)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                "metadata.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            archive.writestr("weights.bin", weights)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _positive_integer(metadata: dict, key: str) -> int:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"مقدار {key} در مدل سبک معتبر نیست.")
    return value


def _resolve_model_path(model_dir: Path) -> Path:
    local_path = Path(model_dir) / MODEL_FILENAME
    if local_path.is_file():
        return local_path
    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "captcha-model" / MODEL_FILENAME
        if bundled.is_file():
            return bundled
    raise FileNotFoundError(f"مدل سبک CAPTCHA پیدا نشد: {local_path}")


def load_model(model_dir: Path):
    """Load and strictly validate an inference-only model bundle."""
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("برای پیش‌بینی CAPTCHA، کتابخانه Pillow لازم است.") from error

    path = _resolve_model_path(model_dir)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if names != {"metadata.json", "weights.bin"}:
                raise ValueError("ساختار فایل مدل سبک معتبر نیست.")
            metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
            weights_bytes = archive.read("weights.bin")
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise ValueError("فایل مدل سبک CAPTCHA معتبر نیست.") from error

    if not isinstance(metadata, dict):
        raise ValueError("Metadata مدل سبک معتبر نیست.")
    format_version = _positive_integer(metadata, "format_version")
    sequence_length = _positive_integer(metadata, "sequence_length")
    width = _positive_integer(metadata, "width")
    height = _positive_integer(metadata, "height")
    channels = _positive_integer(metadata, "channels")
    classes = _positive_integer(metadata, "classes")
    features = _positive_integer(metadata, "features_per_digit")
    if format_version != MODEL_FORMAT_VERSION:
        raise ValueError("نسخه قالب مدل سبک پشتیبانی نمی‌شود.")
    if (width, height, channels, classes) != (
        MODEL_WIDTH,
        MODEL_HEIGHT,
        MODEL_CHANNELS,
        MODEL_CLASSES,
    ):
        raise ValueError("ابعاد مدل سبک با ورودی برنامه سازگار نیست.")
    if MODEL_WIDTH % sequence_length:
        raise ValueError("طول خروجی مدل سبک با عرض تصویر سازگار نیست.")
    expected_features = height * (width // sequence_length) * channels
    if features != expected_features:
        raise ValueError("تعداد ویژگی‌های مدل سبک معتبر نیست.")
    if hashlib.sha256(weights_bytes).hexdigest() != metadata.get("weights_sha256"):
        raise ValueError("صحت وزن‌های مدل سبک تأیید نشد.")

    expected_float_count = features * classes + classes
    if len(weights_bytes) != expected_float_count * 4:
        raise ValueError("اندازه وزن‌های مدل سبک معتبر نیست.")
    values = array("f")
    values.frombytes(weights_bytes)
    if sys.byteorder != "little":
        values.byteswap()
    kernel_count = features * classes
    return {
        "metadata": metadata,
        "kernel": values[:kernel_count],
        "bias": values[kernel_count:],
        "image_module": Image,
        "path": path,
    }


def predict_image(image_bytes: bytes, model_bundle) -> tuple[str, float]:
    """Predict digits and minimum confidence with the exported dense weights."""
    if not image_bytes:
        raise ValueError("تصویر CAPTCHA خالی است.")
    metadata = model_bundle["metadata"]
    kernel = model_bundle["kernel"]
    bias = model_bundle["bias"]
    Image = model_bundle["image_module"]
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            raw = image.convert("RGB").resize(
                (MODEL_WIDTH, MODEL_HEIGHT), Image.Resampling.LANCZOS
            ).tobytes()
    except (OSError, ValueError) as error:
        raise ValueError("تصویر CAPTCHA معتبر نیست.") from error

    sequence_length = int(metadata["sequence_length"])
    region_width = MODEL_WIDTH // sequence_length
    features = int(metadata["features_per_digit"])
    predictions = []
    confidences = []
    row_region_bytes = region_width * MODEL_CHANNELS
    full_row_bytes = MODEL_WIDTH * MODEL_CHANNELS
    for position in range(sequence_length):
        logits = [float(value) for value in bias]
        feature_index = 0
        horizontal_offset = position * row_region_bytes
        for row in range(MODEL_HEIGHT):
            start = row * full_row_bytes + horizontal_offset
            for channel_value in raw[start : start + row_region_bytes]:
                foreground = 1.0 - channel_value / 255.0
                kernel_offset = feature_index * MODEL_CLASSES
                for digit in range(MODEL_CLASSES):
                    logits[digit] += foreground * kernel[kernel_offset + digit]
                feature_index += 1
        if feature_index != features:
            raise RuntimeError("تعداد ویژگی‌های تصویر با مدل سبک سازگار نیست.")
        peak = max(logits)
        exponentials = [math.exp(value - peak) for value in logits]
        total = sum(exponentials)
        probabilities = [value / total for value in exponentials]
        digit = max(range(MODEL_CLASSES), key=probabilities.__getitem__)
        predictions.append(str(digit))
        confidences.append(probabilities[digit])

    text = "".join(predictions)
    if len(text) != sequence_length:
        raise RuntimeError("طول خروجی مدل سبک با Metadata سازگار نیست.")
    return text, min(confidences)
