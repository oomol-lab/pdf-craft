from .translation.translator import translate, translate_async

translate_epub = translate
translate_epub_async = translate_async

__all__ = ["translate", "translate_async", "translate_epub", "translate_epub_async"]
