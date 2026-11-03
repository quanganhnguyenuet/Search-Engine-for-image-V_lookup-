from typing import Protocol


class ImagePreprocessor(Protocol):
    """Normalizes image bytes before AI processing."""

    async def preprocess(self, image: bytes) -> bytes: ...


class OcrEngine(Protocol):
    """Extracts visible text from an image."""

    async def extract_text(self, image: bytes) -> str: ...


class VisionLanguageModel(Protocol):
    """Produces a textual representation of an image."""

    async def describe(self, image: bytes) -> str: ...

