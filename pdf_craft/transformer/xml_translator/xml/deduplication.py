from xml.etree.ElementTree import Element

from .const import ID_KEY
from .xml import iter_with_stack

_SUFFIX = "__translated"


def deduplicate_ids_in_element(element: Element) -> Element:
    seen_ids: set[str] = set()
    original_id_count: dict[str, int] = {}

    for _, sub_element in iter_with_stack(element):
        if ID_KEY not in sub_element.attrib:
            continue
        original_id = sub_element.attrib[ID_KEY]

        if original_id not in seen_ids:
            seen_ids.add(original_id)
            original_id_count[original_id] = 1
        else:
            original_id_count[original_id] = original_id_count.get(original_id, 1) + 1
            occurrence = original_id_count[original_id]

            if occurrence == 2:
                new_id = f"{original_id}{_SUFFIX}"
            else:
                new_id = f"{original_id}{_SUFFIX}_{occurrence - 1}"

            counter = occurrence - 1
            while new_id in seen_ids:
                counter += 1
                new_id = f"{original_id}{_SUFFIX}_{counter}"

            sub_element.attrib["id"] = new_id
            seen_ids.add(new_id)

    return element
