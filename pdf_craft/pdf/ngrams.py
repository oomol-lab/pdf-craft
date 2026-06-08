_HASH_BASE = 131
_HASH_MOD = (1 << 61) - 1  # Mersenne prime — low collision probability


def _rolling_hashes(text: str, n: int) -> list[int]:
    length = len(text)
    if length < n:
        return []

    power = pow(_HASH_BASE, n, _HASH_MOD)
    hashes: list[int] = [0] * (length - n + 1)

    h = 0
    for ch in text[:n]:
        h = (h * _HASH_BASE + ord(ch)) % _HASH_MOD
    hashes[0] = h

    for i in range(1, length - n + 1):
        h = (h * _HASH_BASE - ord(text[i - 1]) * power + ord(text[i + n - 1])) % _HASH_MOD
        hashes[i] = h

    return hashes


def has_repetitive_ngrams(
    text: str,
    min_ngram: int,
    max_ngram: int,
    repeat_threshold: int,
) -> bool:
    """
    检测文本中是否存在大量重复的 n-gram 模式（字符级别）。

    这个方法用于检测 OCR 模型的 "Neural Text Degeneration" 问题。
    当 Transformer 模型训练不足或解码参数配置不当时，会生成重复的文本片段，
    例如 "这是一个例子这是一个例子这是一个例子..." 或
         "this is a test this is a test this is a test..."
    这种重复模式是无意义的噪声，需要被识别和过滤。

    采用字符级别分词以支持中文等没有空格分隔的语言。
    """
    if not text:
        return False

    length = len(text)
    if length < min_ngram * repeat_threshold:
        return False

    for n in range(min_ngram, min(max_ngram + 1, length // repeat_threshold + 1)):
        hashes = _rolling_hashes(text, n)
        for i in range(length - n * repeat_threshold + 1):
            ref_hash = hashes[i]
            ref = text[i : i + n]
            consecutive = 1
            pos = i + n
            while pos + n <= length:
                # O(1) hash check; O(n) string compare only on a hash match
                if hashes[pos] == ref_hash and text[pos : pos + n] == ref:
                    consecutive += 1
                    pos += n
                    if consecutive >= repeat_threshold:
                        return True
                else:
                    break

    return False
