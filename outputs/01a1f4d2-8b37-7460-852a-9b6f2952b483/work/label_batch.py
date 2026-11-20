from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np
import tensorflow as tf
from PIL import Image


DATASET = Path(r"C:\Users\lenovo\AppData\Local\ft-nioc-uploader\captcha-data")
BATCH = Path(
    r"F:\Documents\ChatGPT\ft.nioc.ir\outputs"
    r"\01a1f4d2-8b37-7460-852a-9b6f2952b483\work"
    r"\batch-20261117-143112-300"
)
OUTPUT = BATCH.parent / "automatic-labels.csv"
REPORT = BATCH.parent / "automatic-labels-report.json"

HEIGHT = 50
WIDTH = 160
SEQUENCE_LENGTH = 4
REGION_WIDTH = WIDTH // SEQUENCE_LENGTH
PAD = 5
FOLDS = 5


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def image_array(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        gray = image.convert("L").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        array = np.asarray(gray, dtype=np.float32)
    return 1.0 - array / 255.0


def pixel_hash(path: Path) -> str:
    with Image.open(path) as image:
        pixels = image.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).tobytes()
    return hashlib.sha256(pixels).hexdigest()


def regions(image: np.ndarray) -> np.ndarray:
    padded = np.pad(image, ((0, 0), (PAD, PAD)), constant_values=0.0)
    pieces = []
    for position in range(SEQUENCE_LENGTH):
        start = position * REGION_WIDTH
        pieces.append(padded[:, start : start + REGION_WIDTH + 2 * PAD])
    return np.asarray(pieces, dtype=np.float32)[..., np.newaxis]


def load_unique_dataset() -> tuple[np.ndarray, np.ndarray, dict[str, str]]:
    by_hash: dict[str, tuple[np.ndarray, str]] = {}
    hash_labels: dict[str, str] = {}
    for row in read_rows(DATASET / "labels.csv"):
        label = (row.get("label") or "").strip()
        if len(label) != SEQUENCE_LENGTH or not label.isdigit():
            continue
        path = DATASET / row["filename"]
        if not path.is_file():
            continue
        digest = pixel_hash(path)
        prior = hash_labels.get(digest)
        if prior is not None and prior != label:
            raise RuntimeError(f"Conflicting labels for image hash {digest}")
        hash_labels[digest] = label
        by_hash.setdefault(digest, (image_array(path), label))

    images = np.stack([value[0] for value in by_hash.values()])
    labels = np.asarray(
        [[int(digit) for digit in value[1]] for value in by_hash.values()],
        dtype=np.int32,
    )
    return images, labels, hash_labels


def build_model(seed: int) -> tf.keras.Model:
    tf.keras.utils.set_random_seed(seed)
    inputs = tf.keras.Input((HEIGHT, WIDTH, 1))
    x = tf.keras.layers.RandomTranslation(0.04, 0.025, fill_mode="constant", seed=seed)(inputs)
    x = tf.keras.layers.RandomRotation(0.015, fill_mode="constant", seed=seed + 1)(x)
    x = tf.keras.layers.Conv2D(32, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.MaxPooling2D()(x)
    x = tf.keras.layers.Conv2D(64, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.MaxPooling2D()(x)
    x = tf.keras.layers.Conv2D(128, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.MaxPooling2D()(x)
    x = tf.keras.layers.Flatten()(x)
    x = tf.keras.layers.Dense(256, activation="relu")(x)
    x = tf.keras.layers.Dropout(0.30, seed=seed + 2)(x)
    x = tf.keras.layers.Dense(SEQUENCE_LENGTH * 10)(x)
    x = tf.keras.layers.Reshape((SEQUENCE_LENGTH, 10))(x)
    outputs = tf.keras.layers.Softmax(axis=-1)(x)
    model = tf.keras.Model(inputs, outputs)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def captcha_tensor(images: np.ndarray) -> np.ndarray:
    return images[..., np.newaxis]


def predict_captchas(model: tf.keras.Model, images: np.ndarray) -> np.ndarray:
    return model.predict(captcha_tensor(images), batch_size=64, verbose=0)


def exact_match(probabilities: np.ndarray, labels: np.ndarray) -> float:
    predictions = np.argmax(probabilities, axis=-1)
    return float(np.mean(np.all(predictions == labels, axis=1)))


def main() -> None:
    dataset_images, dataset_labels, hash_labels = load_unique_dataset()
    batch_rows = read_rows(BATCH / "labels.csv")
    batch_paths = [BATCH / row["filename"] for row in batch_rows]
    batch_images = np.stack([image_array(path) for path in batch_paths])

    rng = np.random.default_rng(20261119)
    order = rng.permutation(len(dataset_images))
    folds = np.array_split(order, FOLDS)
    batch_probabilities = []
    validation_scores = []
    out_of_fold = np.zeros((len(dataset_images), SEQUENCE_LENGTH, 10), dtype=np.float32)

    for fold_number, validation_indexes in enumerate(folds, start=1):
        train_indexes = np.setdiff1d(order, validation_indexes, assume_unique=True)
        x_train = captcha_tensor(dataset_images[train_indexes])
        y_train = dataset_labels[train_indexes]
        x_validation = captcha_tensor(dataset_images[validation_indexes])
        y_validation = dataset_labels[validation_indexes]
        model = build_model(4200 + fold_number)
        callbacks = [
            tf.keras.callbacks.EarlyStopping(
                monitor="val_loss", patience=8, restore_best_weights=True
            ),
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss", factor=0.5, patience=4, min_lr=0.00005
            ),
        ]
        history = model.fit(
            x_train,
            y_train,
            validation_data=(x_validation, y_validation),
            epochs=60,
            batch_size=32,
            callbacks=callbacks,
            verbose=0,
        )
        validation_probabilities = predict_captchas(
            model, dataset_images[validation_indexes]
        )
        out_of_fold[validation_indexes] = validation_probabilities
        score = exact_match(
            validation_probabilities, dataset_labels[validation_indexes]
        )
        validation_scores.append(score)
        batch_probabilities.append(predict_captchas(model, batch_images))
        print(
            json.dumps(
                {
                    "fold": fold_number,
                    "epochs": len(history.history["loss"]),
                    "exact_match": score,
                }
            ),
            flush=True,
        )

    ensemble = np.mean(np.stack(batch_probabilities), axis=0)
    ensemble_digits = np.argmax(ensemble, axis=-1)
    ensemble_confidence = np.min(np.max(ensemble, axis=-1), axis=1)
    model_digits = np.stack(
        [np.argmax(probabilities, axis=-1) for probabilities in batch_probabilities]
    )
    unanimous = np.all(model_digits == model_digits[0:1], axis=(0, 2))

    exact_overrides = 0
    output_rows = []
    for index, (row, path) in enumerate(zip(batch_rows, batch_paths, strict=True)):
        digest = pixel_hash(path)
        known_label = hash_labels.get(digest)
        prediction = "".join(str(int(value)) for value in ensemble_digits[index])
        source = "ensemble"
        if known_label is not None:
            prediction = known_label
            source = "exact_dataset_match"
            exact_overrides += 1
        output_rows.append(
            {
                "filename": row["filename"],
                "label": prediction,
                "confidence": f"{ensemble_confidence[index]:.6f}",
                "unanimous": "yes" if bool(unanimous[index]) else "no",
                "source": source,
                **{
                    f"fold_{fold + 1}": "".join(
                        str(int(value)) for value in model_digits[fold, index]
                    )
                    for fold in range(FOLDS)
                },
            }
        )

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)

    oof_score = exact_match(out_of_fold, dataset_labels)
    report = {
        "dataset_unique_images": int(len(dataset_images)),
        "batch_images": int(len(batch_images)),
        "fold_exact_match": validation_scores,
        "out_of_fold_exact_match": oof_score,
        "exact_dataset_overrides": exact_overrides,
        "unanimous_predictions": int(np.sum(unanimous)),
        "confidence_at_least_0_90": int(np.sum(ensemble_confidence >= 0.90)),
        "confidence_at_least_0_75": int(np.sum(ensemble_confidence >= 0.75)),
        "minimum_confidence": float(np.min(ensemble_confidence)),
        "mean_confidence": float(np.mean(ensemble_confidence)),
        "output": str(OUTPUT),
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
