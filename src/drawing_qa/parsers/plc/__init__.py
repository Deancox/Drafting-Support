"""PLC drawing parser (schematic sheets).

Every sheet: abbreviated wire numbers are expanded (wires.py); notes naming slots are read (slot_refs.py).
Template readers, first match wins:
  - RackLayoutReader: rack layout sheet (slot numbers, module per slot, schematic sheet per slot)
  - SlotSheetReader:  one I/O module per sheet (header block, channel rows)
Other schematic sheets (title, index, power, relays) get identifiers and wires only.
"""

from drawing_qa.model.claims import Claim
from drawing_qa.model.enums import DocType
from drawing_qa.model.source import Drawing
from drawing_qa.parsers.base import DrawingParser, ParseContext
from drawing_qa.parsers.plc.rack_layout import RackLayoutReader
from drawing_qa.parsers.plc.slot_refs import read_slot_refs
from drawing_qa.parsers.plc.slot_sheet import SlotSheetReader
from drawing_qa.parsers.plc.legend import read_legend
from drawing_qa.parsers.pins import read_pins
from drawing_qa.parsers.plc.wires import expand_wires
from drawing_qa.pdf.extract import PageContent


class PlcParser(DrawingParser):
    doc_type = DocType.PLC
    version = "0.2.0"
    template_readers = (RackLayoutReader, SlotSheetReader)
    report_unmatched = False

    def supplement(self, page: PageContent, drawing: Drawing, ctx: ParseContext, prior: list[Claim]) -> list[Claim]:
        wires = expand_wires(page, drawing, ctx, self)
        pins = read_pins(page, drawing, ctx, self, prior + wires)
        return wires + pins + read_legend(page, drawing, ctx, self) + read_slot_refs(page, drawing, ctx, self)

    @property
    def planned_content(self) -> str:
        return "device symbol positions, safety references"

    @property
    def built_content(self) -> str:
        return "rack layout, modules, slots, channels, descriptions, devices / JBs / wires per channel, abbreviated wire numbers, route legends"
