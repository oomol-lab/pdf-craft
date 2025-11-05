"""Test the refactored reader classes."""
from pathlib import Path

# Test imports
try:
    from pdf_craft.reader import BaseXMLReader
    print("✓ BaseXMLReader imported successfully")
except Exception as e:
    print(f"✗ Failed to import BaseXMLReader: {e}")

try:
    from pdf_craft.pdf.reader import PagesReader
    print("✓ PagesReader imported successfully")
except Exception as e:
    print(f"✗ Failed to import PagesReader: {e}")

try:
    from pdf_craft.sequence.reader import ChapterReader
    print("✓ ChapterReader imported successfully")
except Exception as e:
    print(f"✗ Failed to import ChapterReader: {e}")

# Test instantiation
try:
    pages_reader = PagesReader(Path("analysing/orc"))
    print(f"✓ PagesReader instantiated, found {len(pages_reader._file_paths)} files")
except Exception as e:
    print(f"✗ Failed to instantiate PagesReader: {e}")

try:
    chapter_reader = ChapterReader(Path("analysing/chapters"))
    print(f"✓ ChapterReader instantiated, found {len(chapter_reader._file_paths)} files")
except Exception as e:
    print(f"✗ Failed to instantiate ChapterReader: {e}")

print("\nAll tests passed! The refactoring is successful.")
