import sys
import unittest

try:
    loader = unittest.TestLoader()

    # 如果提供了命令行参数，只运行指定的测试文件
    if len(sys.argv) > 1:
        pattern = sys.argv[1]
        if not pattern.endswith(".py"):
            pattern = f"{pattern}.py"
    else:
        pattern = "test*.py"

    suite = loader.discover(
        start_dir="tests",
        pattern=pattern,
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        # pylint: disable=consider-using-sys-exit
        exit(1)

# pylint: disable=broad-exception-caught
except Exception as e:
    print(e)
    exit(1)
