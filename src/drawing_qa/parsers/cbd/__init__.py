"""Cable block diagram parser: identifiers and placeholders, plus routing from the drawn
lines (routing.py): enclosures, devices inside them, and cables between them."""

from drawing_qa.model.enums import DocType
from drawing_qa.parsers.base import DrawingParser
from drawing_qa.parsers.cbd.routing import CableRoutingReader


class CableBlockParser(DrawingParser):
    doc_type = DocType.CBD
    version = "0.2.0"
    template_readers = (CableRoutingReader,)

    @property
    def planned_content(self) -> str:
        return "route codes, cable cores vs terminations"

    @property
    def built_content(self) -> str:
        return "enclosures (JB / LCS / panel) and the devices in them, cables between them with spec and number"
