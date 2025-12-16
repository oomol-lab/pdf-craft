import ahocorasick


class SubstringMatcher:
    def __init__(self):
        self._automaton = ahocorasick.Automaton()
        self._next_id = 0
        self._substring_to_ids: dict[str, list[int]] = {}
        self._finalized = False

    def register_substring(self, substring: str) -> int:
        current_id = self._next_id
        self._next_id += 1

        if substring not in self._substring_to_ids:
            self._substring_to_ids[substring] = []
            self._automaton.add_word(substring, substring)

        self._substring_to_ids[substring].append(current_id)
        self._finalized = False

        return current_id

    def match(self, text: str) -> dict[str, tuple[int, list[int]]]:
        if not self._finalized:
            self._automaton.make_automaton()
            self._finalized = True

        match_counts: dict[str, int] = {}
        for _, substring in self._automaton.iter(text):
            match_counts[substring] = match_counts.get(substring, 0) + 1

        match_result: dict[str, tuple[int, list[int]]] = {}
        for substring, count in match_counts.items():
            match_result[substring] = (count, self._substring_to_ids[substring])

        return match_result
