"""Linker (milestone M3b): joins claims from all parsers into the common engineering model.

See IMPLEMENTATION_PLAN.md section 5 and DATA_MODEL.md section 4.
"""

from drawing_qa.linker.linker import link, node_id

__all__ = ["link", "node_id"]
