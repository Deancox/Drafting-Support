"""Termination drawing parser (M2): identifiers and placeholders, plus two template readers:

  - JB termination diagrams (jb_strip.py): JB terminal strips, the device each terminal serves,
    its destination panel and PLC slot, the multicore to the panel, and the wires at the field device;
  - PLC slot termination diagrams (slot_strip.py): the wires on each module terminal and the JB
    each goes out to.
"""

from drawing_qa.model.enums import DocType
from drawing_qa.parsers.base import DrawingParser
from drawing_qa.parsers.pins import read_pins
from drawing_qa.parsers.termination.jb_strip import JbStripReader
from drawing_qa.parsers.termination.slot_strip import SlotStripReader


class TerminationParser(DrawingParser):
    doc_type = DocType.TERM
    version = "0.2.0"
    template_readers = (SlotStripReader, JbStripReader)

    def supplement(self, page, drawing, ctx, prior):
        return read_pins(page, drawing, ctx, self, prior)

    @property
    def planned_content(self) -> str:
        return "route legend codes"

    @property
    def built_content(self) -> str:
        return ("JB terminal strips (terminal, wire, device, destination panel and slot), multicore cores and spares, "
                "wires at the field device, PLC slot terminals and the JB each goes out to")
