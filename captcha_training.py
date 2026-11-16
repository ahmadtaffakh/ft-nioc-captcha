"""Dataset and model helpers for a fixed-length Persian digit CAPTCHA."""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import time
import uuid
from pathlib import Path


PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ASCII_DIGITS = "0123456789"
MODEL_WIDTH = 160
MODEL_HEIGHT = 50
MODEL_CHANNELS = 3

_DIGIT_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS,
    ASCII_DIGITS + ASCII_DIGITS,
)


def normalize_digits(value: str) -> str:
    """Normalize Persian and Arabic-Indic digits to ASCII and validate them."""
    normalized = value.strip().translate(_DIGIT_TRANSLATION)
    if not normalized or not normalized.isascii() or not normalized.isdigit():
        raise ValueError("برچسب باید فقط شامل ارقام فارسی، عربی یا انگلیسی باشد.")
    return normalized


def decode_image_data_url(value: str) -> bytes:
    """Decode an image data URL returned by the CAPTCHA element."""
    header, separator, payload = value.partition(",")
    if not separator or not header.startswith("data:image/") or ";base64" not in header:
        raise ValueError("آدرس تصویر از نوع data:image/...;base64 نیست.")
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("داده Base64 تصویر CAPTCHA معتبر نیست.") from error
    if not image_bytes:
        raise ValueError("تصویر CAPTCHA خالی است.")
    return image_bytes


def dataset_paths(dataset_dir: Path) -> tuple[Path, Path]:
    dataset_dir = Path(dataset_dir)
    return dataset_dir / "images", dataset_dir / "labels.csv"


def save_unlabeled_batch_sample(
    batch_dir: Path, image_bytes: bytes, index: int
) -> Path:
    """Store one captured image and append an empty Excel-editable CSV row."""
    if not image_bytes:
        raise ValueError("تصویر CAPTCHA خالی است.")
    if index < 1:
        raise ValueError("شماره نمونه باید مثبت باشد.")

    images_dir, labels_path = dataset_paths(batch_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    image_path = images_dir / f"captcha-{index:06d}.png"
    if image_path.exists():
        raise FileExistsError(f"نمونه تکراری است: {image_path.name}")

    temporary_image = image_path.with_suffix(".tmp")
    temporary_image.write_bytes(image_bytes)
    os.replace(temporary_image, image_path)
    try:
        write_header = not labels_path.exists() or labels_path.stat().st_size == 0
        with labels_path.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["filename", "label"])
            if write_header:
                writer.writeheader()
            writer.writerow(
                {"filename": f"images/{image_path.name}", "label": ""}
            )
    except Exception:
        image_path.unlink(missing_ok=True)
        raise
    return image_path


def import_labeled_batch(
    batch_dir: Path,
    dataset_dir: Path,
    expected_length: int | None = None,
) -> int:
    """Validate a completed batch CSV, then add all its samples to the dataset."""
    batch_dir = Path(batch_dir)
    _, labels_path = dataset_paths(batch_dir)
    if not labels_path.is_file():
        raise ValueError("فایل labels.csv در پوشه Batch پیدا نشد.")

    samples = []
    with labels_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"filename", "label"}.issubset(reader.fieldnames):
            raise ValueError("فایل Batch باید ستون‌های filename و label داشته باشد.")
        for row_number, row in enumerate(reader, start=2):
            filename = (row.get("filename") or "").strip()
            try:
                label = normalize_digits(row.get("label") or "")
            except ValueError as error:
                raise ValueError(f"برچسب ردیف {row_number} معتبر نیست.") from error
            if expected_length is not None and len(label) != expected_length:
                raise ValueError(
                    f"طول برچسب ردیف {row_number} باید دقیقاً {expected_length} رقم باشد."
                )
            image_path = (batch_dir / filename).resolve()
            try:
                image_path.relative_to(batch_dir.resolve())
            except ValueError as error:
                raise ValueError(f"مسیر تصویر ردیف {row_number} نامعتبر است.") from error
            if not image_path.is_file():
                raise ValueError(f"تصویر ردیف {row_number} پیدا نشد: {filename}")
            samples.append((image_path.read_bytes(), label))

    if not samples:
        raise ValueError("Batch هیچ نمونه‌ای ندارد.")
    for image_bytes, label in samples:
        save_labeled_sample(dataset_dir, image_bytes, label, expected_length)
    return len(samples)


def read_labels(dataset_dir: Path) -> list[tuple[str, str]]:
    """Read validated (relative image path, ASCII label) rows."""
    _, labels_path = dataset_paths(dataset_dir)
    if not labels_path.exists():
        return []

    rows = []
    with labels_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            filename = (row.get("filename") or "").strip()
            label = normalize_digits(row.get("label") or "")
            if filename:
                rows.append((filename, label))
    return rows


def save_labeled_sample(
    dataset_dir: Path,
    image_bytes: bytes,
    label: str,
    expected_length: int | None = None,
) -> Path:
    """Atomically store one image and append its normalized label."""
    normalized = normalize_digits(label)
    if expected_length is not None and len(normalized) != expected_length:
        raise ValueError(f"طول برچسب باید دقیقاً {expected_length} رقم باشد.")
    if not image_bytes:
        raise ValueError("ابتدا تصویر CAPTCHA را دریافت کنید.")

    images_dir, labels_path = dataset_paths(dataset_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    filename = f"captcha-{time.time_ns()}-{uuid.uuid4().hex[:8]}.png"
    image_path = images_dir / filename
    temporary_image = image_path.with_suffix(".tmp")
    temporary_image.write_bytes(image_bytes)
    os.replace(temporary_image, image_path)

    existing = read_labels(dataset_dir)
    temporary_labels = labels_path.with_suffix(".tmp")
    try:
        with temporary_labels.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["filename", "label"])
            writer.writeheader()
            for existing_filename, existing_label in existing:
                writer.writerow(
                    {"filename": existing_filename, "label": existing_label}
                )
            writer.writerow({"filename": f"images/{filename}", "label": normalized})
        os.replace(temporary_labels, labels_path)
    except Exception:
        image_path.unlink(missing_ok=True)
        temporary_labels.unlink(missing_ok=True)
        raise
    return image_path


def validate_dataset(
    dataset_dir: Path, expected_length: int | None = None
) -> tuple[list[tuple[Path, str]], int]:
    """Return existing samples and their one consistent sequence length."""
    samples = []
    lengths = set()
    dataset_dir = Path(dataset_dir)
    for filename, label in read_labels(dataset_dir):
        image_path = dataset_dir / filename
        if not image_path.is_file():
            raise ValueError(f"تصویر ثبت‌شده پیدا نشد: {filename}")
        samples.append((image_path, label))
        lengths.add(len(label))

    if not samples:
        raise ValueError("هنوز نمونه برچسب‌خورده‌ای وجود ندارد.")
    if len(lengths) != 1:
        raise ValueError("طول برچسب‌های dataset یکسان نیست.")

    sequence_length = lengths.pop()
    if expected_length is not None and sequence_length != expected_length:
        raise ValueError(
            f"طول نمونه‌ها {sequence_length} است، اما طول تنظیم‌شده {expected_length} است."
        )
    return samples, sequence_length


def _import_ml_dependencies():
    try:
        import numpy as np
        from PIL import Image
        import tensorflow as tf
    except ImportError as error:
        raise RuntimeError(
            "برای آموزش/پیش‌بینی، Python 3.10 تا 3.13 و وابستگی‌های ML را نصب کنید."
        ) from error
    return np, Image, tf


def _load_image(image_source, image_module, np_module):
    if isinstance(image_source, (bytes, bytearray)):
        source = io.BytesIO(image_source)
    else:
        source = image_source
    with image_module.open(source) as image:
        rgb_image = image.convert("RGB").resize(
            (MODEL_WIDTH, MODEL_HEIGHT), image_module.Resampling.LANCZOS
        )
        array = np_module.asarray(rgb_image, dtype="float32") / 255.0
    return array


def build_model(sequence_length: int):
    """Build a compact segmentation-free CNN with one 10-class head per position."""
    _, _, tf = _import_ml_dependencies()
    layers = tf.keras.layers
    inputs = tf.keras.Input(
        (MODEL_HEIGHT, MODEL_WIDTH, MODEL_CHANNELS), name="captcha"
    )
    x = layers.RandomContrast(0.15)(inputs)
    x = layers.RandomTranslation(0.04, 0.04, fill_mode="nearest")(x)
    for filters in (24, 48, 72):
        x = layers.Conv2D(filters, 3, padding="same", activation="relu")(x)
        x = layers.BatchNormalization()(x)
        x = layers.MaxPooling2D(2)(x)
    x = layers.AveragePooling2D(pool_size=(6, 1), name="height_pool")(x)
    x = layers.Flatten()(x)
    x = layers.Dense(64, activation="relu")(x)
    x = layers.Dropout(0.25)(x)
    x = layers.Dense(sequence_length * 10)(x)
    x = layers.Reshape((sequence_length, 10))(x)
    outputs = layers.Softmax(axis=-1, name="digits")(x)
    model = tf.keras.Model(inputs, outputs, name="ft_captcha_multihead")
    model.compile(
        optimizer="adam",
        loss="sparse_categorical_crossentropy",
        metrics=["sparse_categorical_accuracy"],
    )
    return model


def train_model(
    dataset_dir: Path,
    model_dir: Path,
    expected_length: int,
    epochs: int = 40,
) -> dict:
    """Train, evaluate by exact sequence match, and atomically publish the model."""
    np, Image, tf = _import_ml_dependencies()
    samples, sequence_length = validate_dataset(dataset_dir, expected_length)
    if len(samples) < 20:
        raise ValueError("برای آموزش حداقل ۲۰ نمونه برچسب‌خورده لازم است.")

    rng = np.random.default_rng(42)
    order = rng.permutation(len(samples))
    images = np.stack(
        [_load_image(samples[index][0], Image, np) for index in order]
    )
    labels = np.asarray(
        [[int(digit) for digit in samples[index][1]] for index in order],
        dtype="int32",
    )
    validation_size = max(1, round(len(samples) * 0.2))
    x_validation, y_validation = images[:validation_size], labels[:validation_size]
    x_train, y_train = images[validation_size:], labels[validation_size:]

    model = build_model(sequence_length)
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=6, restore_best_weights=True
        )
    ]
    history = model.fit(
        x_train,
        y_train,
        validation_data=(x_validation, y_validation),
        epochs=max(1, epochs),
        batch_size=min(32, len(x_train)),
        callbacks=callbacks,
        verbose=0,
    )

    probabilities = model.predict(x_validation, verbose=0)
    predictions = np.argmax(probabilities, axis=-1)
    exact_match = float(np.mean(np.all(predictions == y_validation, axis=1)))

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "captcha_model.keras"
    metadata_path = model_dir / "captcha_model.json"
    temporary_model = model_dir / "captcha_model.tmp.keras"
    temporary_metadata = model_dir / "captcha_model.tmp.json"
    model.save(temporary_model)
    temporary_metadata.write_text(
        json.dumps(
            {
                "sequence_length": sequence_length,
                "width": MODEL_WIDTH,
                "height": MODEL_HEIGHT,
                "channels": MODEL_CHANNELS,
                "validation_samples": validation_size,
                "exact_match": exact_match,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary_model, model_path)
    os.replace(temporary_metadata, metadata_path)
    return {
        "model_path": str(model_path),
        "samples": len(samples),
        "epochs": len(history.history["loss"]),
        "exact_match": exact_match,
    }


def load_model(model_dir: Path):
    np, Image, tf = _import_ml_dependencies()
    model_dir = Path(model_dir)
    metadata = json.loads(
        (model_dir / "captcha_model.json").read_text(encoding="utf-8")
    )
    model = tf.keras.models.load_model(model_dir / "captcha_model.keras")
    return model, metadata, np, Image


def predict_image(image_bytes: bytes, model_bundle) -> tuple[str, float]:
    """Predict ASCII digits and return the minimum per-position confidence."""
    model, metadata, np, Image = model_bundle
    image = _load_image(image_bytes, Image, np)
    probabilities = model.predict(image[np.newaxis, ...], verbose=0)[0]
    indexes = np.argmax(probabilities, axis=-1)
    text = "".join(str(int(index)) for index in indexes)
    confidence = float(np.min(np.max(probabilities, axis=-1)))
    if len(text) != int(metadata["sequence_length"]):
        raise RuntimeError("طول خروجی مدل با metadata سازگار نیست.")
    return text, confidence


def predict_batch(batch_dir: Path, model_dir: Path) -> dict:
    """Write model predictions to the third batch CSV column atomically."""
    batch_dir = Path(batch_dir)
    _, labels_path = dataset_paths(batch_dir)
    if not labels_path.is_file():
        raise ValueError("فایل labels.csv در پوشه Batch پیدا نشد.")

    with labels_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"filename", "label"}.issubset(reader.fieldnames):
            raise ValueError("فایل Batch باید ستون‌های filename و label داشته باشد.")
        rows = list(reader)
        extra_fields = [
            field
            for field in reader.fieldnames
            if field not in {"filename", "label", "prediction"}
        ]

    if not rows:
        raise ValueError("Batch هیچ نمونه‌ای ندارد.")

    model_bundle = load_model(model_dir)
    matches = 0
    evaluated = 0
    output_rows = []
    for row_number, row in enumerate(rows, start=2):
        filename = (row.get("filename") or "").strip()
        image_path = (batch_dir / filename).resolve()
        try:
            image_path.relative_to(batch_dir.resolve())
        except ValueError as error:
            raise ValueError(f"مسیر تصویر ردیف {row_number} نامعتبر است.") from error
        if not filename or not image_path.is_file():
            raise ValueError(f"تصویر ردیف {row_number} پیدا نشد: {filename}")

        prediction, _confidence = predict_image(image_path.read_bytes(), model_bundle)
        output_row = {
            field: row.get(field, "")
            for field in ["filename", "label", *extra_fields]
        }
        output_row["prediction"] = prediction
        output_rows.append(output_row)

        label = (row.get("label") or "").strip()
        if label:
            try:
                normalized_label = normalize_digits(label)
            except ValueError as error:
                raise ValueError(f"برچسب ردیف {row_number} معتبر نیست.") from error
            evaluated += 1
            matches += normalized_label == prediction

    fieldnames = ["filename", "label", "prediction", *extra_fields]
    temporary_labels = labels_path.with_suffix(".tmp")
    try:
        with temporary_labels.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(output_rows)
        os.replace(temporary_labels, labels_path)
    except Exception:
        temporary_labels.unlink(missing_ok=True)
        raise

    return {
        "count": len(output_rows),
        "evaluated": evaluated,
        "matches": matches,
        "exact_match": matches / evaluated if evaluated else None,
        "labels_path": str(labels_path),
    }
