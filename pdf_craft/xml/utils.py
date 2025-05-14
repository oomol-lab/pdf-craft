from xml.etree.ElementTree import Element

def clone(element: Element) -> Element:
  new_element = Element(element.tag)
  for attr_name, attr_value in element.items():
    new_element.set(attr_name, attr_value)
  new_element.text = element.text
  for child in element:
    new_child = clone(child)
    new_element.append(new_child)
    new_child.tail = child.tail
  return new_element