"""AI-assisted intake workflow prototype (EPID 695 final project).

Nodes:
  2  transcribe.py    AssemblyAI transcription (atomic protocol)
  3  extract.py       core facts + narrative (Claude)
  4  extract.py       categorize case (Claude)
  5  extract.py       category-specific facts (Claude)
  -  frame.py         build the one-row case DataFrame (glue)
  6  validate.py      Pandera schema validation (atomic protocol)
  7  app.py           volunteer review (Streamlit)
  8  draft_note.py    draft case note (Claude + template)
  9  deidentify.py    Presidio de-identification of the review log (atomic protocol)
"""
