from typing import Literal


class I18N:
  def __init__(self, lan: Literal["zh", "en"]):
    if lan == "zh":
      self.unnamed: str = "未命名"
      self.cover: str = "封面"
      self.references: str = "引用"
    elif lan == "en":
      self.unnamed: str = "Unnamed"
      self.cover: str = "Cover"
      self.references: str = "References"