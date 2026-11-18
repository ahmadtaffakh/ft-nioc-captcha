import io
import json
import tempfile
import unittest
import zipfile
from array import array
from pathlib import Path

from PIL import Image

from captcha_inference import load_model, predict_image, write_model_file


class LightweightInferenceTest(unittest.TestCase):
    def _write_constant_model(self, model_dir: Path, selected_digit=3) -> Path:
        sequence_length = 4
        features = 50 * 40 * 3
        kernel = array("f", [0.0]) * (features * 10)
        bias = array("f", [-2.0]) * 10
        bias[selected_digit] = 2.0
        return write_model_file(
            model_dir / "captcha_model.ftmodel",
            {
                "sequence_length": sequence_length,
                "validation_samples": 20,
                "exact_match": 0.5,
            },
            kernel.tobytes(),
            bias.tobytes(),
        )

    def test_predicts_without_tensorflow_or_numpy(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            self._write_constant_model(model_dir, selected_digit=3)
            image = Image.new("RGB", (160, 50), "white")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")

            text, confidence = predict_image(buffer.getvalue(), load_model(model_dir))

            self.assertEqual(text, "3333")
            self.assertGreater(confidence, 0.8)

    def test_rejects_tampered_weights(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            path = self._write_constant_model(model_dir)
            with zipfile.ZipFile(path) as archive:
                metadata = json.loads(archive.read("metadata.json"))
                weights = bytearray(archive.read("weights.bin"))
            weights[0] ^= 1
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("metadata.json", json.dumps(metadata))
                archive.writestr("weights.bin", weights)

            with self.assertRaisesRegex(ValueError, "صحت وزن"):
                load_model(model_dir)

    def test_rejects_wrong_weight_dimensions_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "ابعاد وزن"):
                write_model_file(
                    Path(temporary) / "captcha_model.ftmodel",
                    {"sequence_length": 4},
                    b"short",
                    b"short",
                )

    def test_rejects_unknown_archive_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            path = self._write_constant_model(model_dir)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("unexpected.txt", "data")

            with self.assertRaisesRegex(ValueError, "ساختار"):
                load_model(model_dir)


if __name__ == "__main__":
    unittest.main()
