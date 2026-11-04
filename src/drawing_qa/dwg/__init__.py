"""Text for sheets plotted without SHX notes, read from the sheet's own AutoCAD drawing (DWG).

A PDF plotted with PDFSHX = 0 carries its line work but no readable text. When the DWG it was plotted from
sits beside it (401104-052.pdf + 401104-052.dwg), the DWG is converted to DXF with the ODA File Converter,
its text is read exactly, and each piece of text is placed on the PDF page by lining the DWG's line work up
with the PDF's. The PDF stays the document checked: its lines are used as they are, and a DWG that does
not line up with it is not used.
"""
