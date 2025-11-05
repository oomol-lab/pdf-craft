from pathlib import Path
from pdf_craft import transform_markdown

def main() -> None:
    project_root = Path(__file__).parent
    assets_dir_path = project_root / "tests" / "assets"
    analysing_dir_path = project_root / "analysing"
    pdf_file_name = "table&formula.pdf"

    transform_markdown(
        pdf_path=assets_dir_path / pdf_file_name,
        markdown_path=analysing_dir_path / "output.md",
        markdown_assets_path=Path("images"),
        analysing_path=analysing_dir_path,
    )

if __name__ == "__main__":
    main()