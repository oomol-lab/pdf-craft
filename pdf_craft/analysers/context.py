from __future__ import annotations

import re
import os
import shutil

from pathlib import Path
from datetime import datetime, timezone
from typing import cast, Any, TypeVar, Generic, TypedDict, Callable
from yaml import safe_load, safe_dump
from xml.etree.ElementTree import Element
from ..xml import encode


CURRENT_STATE_VERSION = "1.0.0"
S = TypeVar("S")

_STATE_FILE = "state.yaml"

class _StateRoot(TypedDict):
  version: str
  created_at: str
  updated_at: str
  payload: Any


class Context(Generic[S]):
  def __init__(self, path: Path, init: Callable[[], S]) -> None:
    self._state: S
    self._path: Path = path
    self._created_at: str

    state: S | None = None
    created_at: str | None = None
    if path.exists():
      assert path.is_dir(), f"Path {path} is not a directory"
      state_path = path.joinpath(_STATE_FILE)
      if state_path.exists():
        state, created_at = self._load_state(state_path)
      if state is None:
        shutil.rmtree(path)

    if state is None:
      path.mkdir(parents=True)
      state = init()
      created_at = self._current_utc()

    self._state = state
    self._created_at = created_at

  def _load_state(self, state_path: Path) -> tuple[S, str] | tuple[None, None]:
    with state_path.open("r", encoding="utf-8") as file:
      root = cast(_StateRoot, safe_load(file))
      version = root["version"]
      if version != CURRENT_STATE_VERSION:
        return None, None
      return cast(S, root["payload"]), root["created_at"]

  @property
  def path(self) -> Path:
    return self._path

  @property
  def state(self) -> S:
    return self._state

  @state.setter
  def state(self, state: S) -> None:
    self._state = state
    file_path = self._path.joinpath(_STATE_FILE)
    self._atomic_write(
      file_path=file_path,
      content=safe_dump({
        "version": CURRENT_STATE_VERSION,
        "created_at": self._created_at,
        "updated_at": self._current_utc(),
        "payload": self._state,
      }),
    )

  def write_xml_file(self, file_path: Path, xml: Element) -> None:
    file_content = encode(xml)
    base_path = file_path.parent
    if not base_path.exists():
      base_path.mkdir(parents=True)
    self._atomic_write(file_path, file_content)

  def _current_utc(self) -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

  def _split_index_suffix(self, file_name: str) -> tuple[str | None, int, int]:
    matches = re.match(r"^[a-zA-Z]+_\d+(_\d+)?\.xml$", file_name)
    if not matches:
      return None, -1, -1

    file_prefix: str
    index1: str
    index2: str
    cells = re.sub(r"\..*$", "", file_name).split("_")
    if len(cells) == 3:
      file_prefix, index1, index2 = cells
    else:
      file_prefix, index1 = cells
      index2 = index1

    return file_prefix, int(index1), int(index2)

  def _atomic_write(self, file_path: str, content: str):
    try:
      with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)
        file.flush()
    except Exception as e:
      if os.path.exists(file_path):
        os.unlink(file_path)
      raise e
