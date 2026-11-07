"""Render the PDF with Poppler and make a contact sheet; no browser required."""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def main():
    with tempfile.TemporaryDirectory(prefix="visualseek-preview-") as tmp:
        subprocess.run(["pdftoppm", "-scale-to", "1280", "-png", str(HERE / "VisualSeek-General-Idea.pdf"), str(Path(tmp) / "slide")], check=True)
        paths = sorted(Path(tmp).glob("slide-*.png"))
        if len(paths) != 8:
            raise ValueError(f"Expected 8 slides, found {len(paths)}")
        preview = Image.new("RGB", (1280, 1480), "#d7ded9")
        for i,path in enumerate(paths):
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((620,349))
                preview.paste(im,(10+(i%2)*640,10+(i//2)*370))
        preview.save(HERE / "preview.png")
        print("Rendered and previewed 8 PDF pages")


if __name__ == "__main__":
    main()
