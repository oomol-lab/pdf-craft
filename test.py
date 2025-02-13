import unittest

try:
  loader = unittest.TestLoader()
  suite = loader.discover(
    start_dir="tests",
    # pattern="test_file_kb.py",
  )
  runner = unittest.TextTestRunner()
  result = runner.run(suite)
  if not result.wasSuccessful():
    # pylint: disable=consider-using-sys-exit
    exit(1)

# pylint: disable=broad-exception-caught
except Exception as e:
  print(e)
  exit(1)
