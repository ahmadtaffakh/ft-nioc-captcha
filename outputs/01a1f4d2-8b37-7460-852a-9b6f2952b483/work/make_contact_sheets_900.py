from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


ROOT = Path(
    r"F:\Documents\ChatGPT\ft.nioc.ir\outputs"
    r"\01a1f4d2-8b37-7460-852a-9b6f2952b483\work"
)
BATCH = ROOT / "batch-20261116-165948-900-source" / "batch-20261116-165948-900"
OUTPUT = ROOT / "contact-sheets-900"
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

print(f"images={len(paths)} sheets={len(list(OUTPUT.glob('sheet-*.png')))}")
