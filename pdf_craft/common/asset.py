import hashlib
import uuid
from pathlib import Path
from typing import Literal

from PIL import Image

AssetRef = Literal["image", "table", "equation"]
ASSET_TAGS: tuple[AssetRef, ...] = ("image", "table", "equation")


class AssetHub:
    def __init__(self, asset_path: Path) -> None:
        self._asset_path = asset_path

    def clip(self, image: Image.Image, det: tuple[int, int, int, int]) -> str:
        cropped_image = image.crop(det)
        self._asset_path.mkdir(parents=True, exist_ok=True)
        temp_filename = f"{uuid.uuid4().hex}.png.temp"
        temp_path = self._asset_path / temp_filename
        try:
            cropped_image.save(temp_path, format="PNG")
            image_hash = self._calculate_file_hash(temp_path)
            target_path = self._asset_path / f"{image_hash}.png"
            if target_path.exists():
                temp_path.unlink()
                return image_hash
            temp_path.rename(target_path)
            return image_hash

        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise e

    def _calculate_file_hash(self, file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
