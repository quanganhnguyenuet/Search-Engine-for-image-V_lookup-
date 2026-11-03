from typing import Protocol

type Vector = tuple[float, ...]


class TextEmbedder(Protocol):
    """Maps text into a model-specific vector space."""

    async def embed_text(self, text: str) -> Vector: ...


class ImageEmbedder(Protocol):
    """Maps an image into a model-specific vector space."""

    async def embed_image(self, image: bytes) -> Vector: ...

