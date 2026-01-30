import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Self

from .executor import LLMExecutor
from .increasable import Increasable, Increaser
from .types import Message, MessageRole

# Global lock for cache file commit operations
_CACHE_COMMIT_LOCK = threading.Lock()


class LLMContext:
    def __init__(
        self,
        executor: LLMExecutor,
        cache_path: Path | None,
        cache_seed_content: str | None,
        top_p: Increasable,
        temperature: Increasable,
    ) -> None:
        self._executor = executor
        self._cache_path = cache_path
        self._cache_seed_content = cache_seed_content
        self._top_p: Increaser = top_p.context()
        self._temperature: Increaser = temperature.context()
        self._context_id = uuid.uuid4().hex[:12]
        self._temp_files: set[Path] = set()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            # Success: commit all temporary cache files
            self._commit()
        else:
            # Failure: rollback (delete) all temporary cache files
            self._rollback()

    def request(
        self,
        input: str | list[Message],
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> str:
        messages: list[Message]
        if isinstance(input, str):
            messages = [Message(role=MessageRole.USER, message=input)]
        else:
            messages = input

        try:
            cache_key: str | None = None
            if self._cache_path is not None:
                cache_key = self._compute_messages_hash(messages)
                permanent_cache_file = self._cache_path / f"{cache_key}.txt"
                if permanent_cache_file.exists():
                    cached_content = permanent_cache_file.read_text(encoding="utf-8")
                    return cached_content

            if temperature is None:
                temperature = self._temperature.current
            if top_p is None:
                top_p = self._top_p.current

            # Make the actual request
            response = self._executor.request(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                cache_key=cache_key,
            )
            # Save to temporary cache if cache_path is set
            if self._cache_path is not None and cache_key is not None:
                temp_cache_file = (
                    self._cache_path / f"{cache_key}.{self._context_id}.txt"
                )
                if temp_cache_file.exists():
                    temp_cache_file.unlink()
                temp_cache_file.write_text(response, encoding="utf-8")
                self._temp_files.add(temp_cache_file)

            return response

        finally:
            self._temperature.increase()
            self._top_p.increase()

    def _compute_messages_hash(self, messages: list[Message]) -> str:
        messages_dict = [
            {"role": msg.role.value, "message": msg.message} for msg in messages
        ]
        hash_data = {
            "messages": messages_dict,
            "cache_seed": self._cache_seed_content,
        }
        hash_json = json.dumps(hash_data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha512(hash_json.encode("utf-8")).hexdigest()

    def _commit(self) -> None:
        for temp_file in sorted(self._temp_files):
            if temp_file.exists():
                # Remove the .[context-id].txt suffix to get permanent name
                permanent_name = temp_file.name.rsplit(".", 2)[0] + ".txt"
                permanent_file = temp_file.parent / permanent_name

                with _CACHE_COMMIT_LOCK:  # 多线程下的线程安全
                    if permanent_file.exists():
                        temp_file.unlink()
                    else:
                        temp_file.rename(permanent_file)

    def _rollback(self) -> None:
        for temp_file in self._temp_files:
            if temp_file.exists():
                temp_file.unlink()
