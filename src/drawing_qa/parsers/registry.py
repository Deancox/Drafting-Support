"""Document type -> parser."""

from drawing_qa.model.enums import DocType
from drawing_qa.parsers.base import DrawingParser
from drawing_qa.parsers.cbd import CableBlockParser
from drawing_qa.parsers.plc import PlcParser
from drawing_qa.parsers.termination import TerminationParser

_PARSERS: dict[DocType, type[DrawingParser]] = {
    DocType.PLC: PlcParser,
    DocType.CBD: CableBlockParser,
    DocType.TERM: TerminationParser,
}


def build_parsers() -> dict[DocType, DrawingParser]:
    return {doc_type: cls() for doc_type, cls in _PARSERS.items()}
