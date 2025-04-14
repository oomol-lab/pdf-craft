import io
import re
import matplotlib.pyplot as plt

from xml.etree.ElementTree import fromstring, Element
from latex2mathml.converter import convert
from ..utils import sha256_hash
from .types import LaTeXRender
from .context import Context


def try_gen_formula(context: Context, element: Element) -> Element | None:
  if context.latex_render == LaTeXRender.CLIPPING:
    return None

  latex: Element | None = None
  for child in element:
    if child.tag == "latex":
      latex = child
      break
  if latex is None:
    return None

  latex_expr = _normalize_expression(latex.text)
  if context.latex_render == LaTeXRender.MATHML:
    try:
      return _latex2mathml(latex_expr)
    except SystemError:
      return None

  elif context.latex_render == LaTeXRender.SVG:
    try:
      svg_image = _latex_formula2svg(latex_expr)
    except Exception:
      return None

    file_name = f"{sha256_hash(svg_image)}.svg"
    img_element = _create_image_element(file_name, element)
    context.add_asset(file_name, "image/svg+xml", svg_image)
    return img_element

def try_gen_asset(context: Context, element: Element) -> Element | None:
  hash = element.get("hash", None)
  if hash is None:
    return None

  file_name = f"{hash}.png"
  context.use_asset(file_name, "image/png")

  return _create_image_element(file_name, element)

_ESCAPE_UNICODE_PATTERN = re.compile(r"&#x([0-9A-Fa-f]{5});")

def _latex2mathml(latex: str):
  # latex2mathml 转义会带上一个奇怪的 `&` 前缀，这显然是多余的
  # 不得已，在这里用正则表达式处理以修正这个错误
  def repl(match):
    hex_code = match.group(1)
    char = chr(int(hex_code, 16))
    if char == "<":
      return "&lt;"
    elif char == ">":
      return "&gt;"
    else:
      return char

  mathml = re.sub(
    pattern=_ESCAPE_UNICODE_PATTERN,
    repl=repl,
    string=convert(latex),
  )
  return fromstring(mathml)

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

def _normalize_expression(expression: str) -> str:
  expression = expression.replace("\n", "")
  expression = expression.strip()
  return expression
