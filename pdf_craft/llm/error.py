import httpx
import requests


def is_retry_error(err: Exception) -> bool:
  if _is_httpx_retry_error(err):
    return True
  if _is_request_retry_error(err):
    return True
  return False

# https://www.python-httpx.org/exceptions/
def _is_httpx_retry_error(err: Exception) -> bool:
    if isinstance(err, httpx.StreamError):
      return True
    if isinstance(err, httpx.TimeoutException):
      return True
    if isinstance(err, httpx.NetworkError):
      return True
    if isinstance(err, httpx.ProtocolError):
      return True
    return False

# https://requests.readthedocs.io/en/latest/api/#exceptions
def _is_request_retry_error(err: Exception) -> bool:
  if isinstance(err, requests.ConnectionError):
    return True
  if isinstance(err, requests.ConnectTimeout):
    return True
  if isinstance(err, requests.ReadTimeout):
    return True
  if isinstance(err, requests.Timeout):
    return True
  return False