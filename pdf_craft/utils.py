from hashlib import sha256


def sha256_hash(data: bytes) -> str:
  hash256 = sha256()
  hash256.update(data)
  return hash256.hexdigest()