import io
import matplotlib.pyplot as plt

from xml.etree.ElementTree import Element
from latex2mathml.converter import convert_to_element
from ..utils import sha256_hash
from .assets import Assets


def try_gen_formula(assets: Assets, element: Element) -> Element | None:
  latex: Element | None = None
  for child in element:
    if child.tag == "latex":
      latex = child
      break
  if latex is None:
    return None

  try:
    print("LaTeX")
    print(latex.text)
    print("")
    # dom = convert_to_element(latex.text)
    svg_image = _latex_formula2svg(latex.text.replace("\n", ""))
    file_name = f"{sha256_hash(svg_image)}.svg"
    img_element = _create_image_element(file_name, element)
    assets.add_asset(file_name, "image/svg+xml", svg_image)
    return img_element

  except SystemError:
    return None

def try_gen_asset(assets: Assets, element: Element) -> Element | None:
  hash = element.get("hash", None)
  if hash is None:
    return None

  file_name = f"{hash}.png"
  assets.use_asset(file_name, "image/png")

  return _create_image_element(file_name, element)

def _latex_formula2svg(latex: str, font_size: int=12):
  # from https://www.cnblogs.com/qizhou/p/18170083
  output = io.BytesIO()
  plt.rc("text", usetex = True)
  plt.rc("font", size = font_size)
  fig, ax = plt.subplots()
  txt = ax.text(0.5, 0.5, f"${latex}$", ha="center", va="center", transform=ax.transAxes)
  ax.axis("off")
  fig.canvas.draw()
  bbox = txt.get_window_extent(renderer=fig.canvas.get_renderer())
  fig.set_size_inches(bbox.width / fig.dpi, bbox.height / fig.dpi)
  plt.savefig(
    output,
    format="svg",
    transparent=True,
    bbox_inches="tight",
    pad_inches=0,
  )
  return output.getvalue()

def _create_image_element(file_name: str, origin: Element):
  img_element = Element("img")
  img_element.set("src", f"../assets/{file_name}")
  alt: str | None = None

  if origin.text:
    alt = origin.text
  if alt is None:
    img_element.set("alt", "image")
  else:
    img_element.set("alt", alt)

  return img_element