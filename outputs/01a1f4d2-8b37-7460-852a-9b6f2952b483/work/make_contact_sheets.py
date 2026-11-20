from pathlib import Path
import csv

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


BATCH = Path(
    r"F:\Documents\ChatGPT\ft.nioc.ir\outputs"
    r"\01a1f4d2-8b37-7460-852a-9b6f2952b483\work"
    r"\batch-20261117-143112-300"
)
OUTPUT = BATCH.parent / "contact-sheets"
OUTPUT.mkdir(parents=True, exist_ok=True)
OUTPUT = BATCH.parent / "contact-sheets"
OUTPUT.mkdir(parents=True, exist_ok=True)

columns = 4
rows = 5
cell_width = 380
cell_height = 145
image_width = 352
image_height = 110
font = ImageFont.truetype("arial.ttf", 18)

paths = sorted((BATCH / "images").glob("*.png"))
for sheet_index, start in enumerate(range(0, len(paths), columns * rows), start=1):
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    draw = ImageDraw.Draw(sheet)
    for offset, path in enumerate(paths[start : start + columns * rows]):
        row, column = divmod(offset, columns)
        left = column * cell_width
        top = row * cell_height
        with Image.open(path) as source:
            image = source.convert("RGB").resize(
                (image_width, image_height), Image.Resampling.LANCZOS
            )
        image = ImageOps.autocontrast(image, cutoff=0.5)
        image = ImageEnhance.Contrast(image).enhance(1.45)
        sheet.paste(image, (left + 14, top + 27))
        number = start + offset + 1
        draw.text((left + 14, top + 4), f"{number:04d}", fill="black", font=font)
        draw.rectangle(
            (left + 8, top + 1, left + cell_width - 8, top + cell_height - 5),
            outline="#B0B0B0",
            width=1,
        )
    sheet.save(OUTPUT / f"sheet-{sheet_index:02d}.png", optimize=True)

print(f"created={len(list(OUTPUT.glob('sheet-*.png')))}")

# Build a labeled digit reference from the already verified local dataset.
dataset = Path(r"C:\Users\lenovo\AppData\Local\ft-nioc-uploader\captcha-data")
with (dataset / "labels.csv").open("r", encoding="utf-8-sig", newline="") as stream:
    known_rows = list(csv.DictReader(stream))
examples = {str(digit): [] for digit in range(10)}
for known in known_rows:
    label = (known.get("label") or "").strip()
    path = dataset / (known.get("filename") or "")
    if len(label) != 4 or not label.isdigit() or not path.is_file():
        continue
    with Image.open(path) as source:
        source = source.convert("RGB").resize((160, 50), Image.Resampling.LANCZOS)
        for position, digit in enumerate(label):
            if len(examples[digit]) >= 5:
                continue
            left = max(0, position * 40 - 5)
            right = min(160, (position + 1) * 40 + 5)
            crop = source.crop((left, 0, right, 50)).resize((180, 180), Image.Resampling.LANCZOS)
            crop = ImageOps.autocontrast(crop, cutoff=0.5)
            crop = ImageEnhance.Contrast(crop).enhance(1.45)
            examples[digit].append(crop)
    if all(len(items) >= 5 for items in examples.values()):
        break

reference = Image.new("RGB", (5 * 200, 10 * 220), "white")
reference_draw = ImageDraw.Draw(reference)
reference_font = ImageFont.truetype("arial.ttf", 24)
for digit in range(10):
    for column, crop in enumerate(examples[str(digit)]):
        left = column * 200
        top = digit * 220
        reference.paste(crop, (left + 10, top + 30))
        reference_draw.text((left + 10, top + 2), str(digit), fill="black", font=reference_font)
reference.save(OUTPUT / "digit-reference.png", optimize=True)
