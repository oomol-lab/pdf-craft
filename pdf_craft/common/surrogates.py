# surrogate characters (U+D800 to U+DFFF)
def remove_surrogates(text: str) -> str:
    return "".join(char for char in text if not (0xD800 <= ord(char) <= 0xDFFF))
