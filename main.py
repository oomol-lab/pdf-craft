from pathlib import Path

from pdf_craft import ocr_pdf


def main() -> None:
    project_root = Path(__file__).parent
    assets_dir_path = project_root / "tests" / "assets"
    analysing_dir_path = project_root / "analysing"
    pdf_file_name = "table&formula.pdf"
    ocr_pdf(
        pdf_path=assets_dir_path / pdf_file_name,
        asset_path=analysing_dir_path / "assets",
        ocr_path=analysing_dir_path / "ocr_output",
        model_size="gundam",
        includes_footnotes=True,
    )

if __name__ == "__main__":
    main()