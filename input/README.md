# Input: the drawing packages

Put each package to review in its own folder here. `Main` is the one Steve reviews; the Drawing QA window can
use any folder.

```
input/
  Main/
    PLC/            PLC schematic sheets (PDF, and the DWG beside it if you have it)
    CableBlocks/    cable block diagrams (PDF)
    Terminations/   termination drawings: JB strips and PLC slot sheets (PDF)
    EquipmentList/  the equipment list (Excel or PDF)
```

Any of the four may be missing; the review checks what is there. Name the files as they are issued
(e.g. `401103-036.pdf`): the sheet number comes from the title block, the file name is the fallback.

The drawings stay on this computer: everything in `input/` except this file and the empty folders' `.gitkeep`
placeholders is left out of git. The tool only reads them, never changes them.
