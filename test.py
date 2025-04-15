import unittest


try:
  loader = unittest.TestLoader()
  suite = loader.discover(
    # pattern="test_serial.py",
    start_dir="tests",
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
