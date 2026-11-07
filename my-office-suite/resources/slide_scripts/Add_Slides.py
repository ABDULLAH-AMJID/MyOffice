"""Create several slides from a loop.

Every slide is a real slide you can edit afterwards; the whole run is a single
undo step (Ctrl+Z removes all of them).
"""
p = app.presentation

topics = ["Overview", "Goals", "Progress", "Risks", "Next steps"]

for i, topic in enumerate(topics, start=1):
    slide = p.add_slide(layout="title_only", title=f"{i}. {topic}")
    slide.shapes.add_textbox(app.emu(90), app.emu(230), app.emu(780), app.emu(140),
                             text=f"Notes for “{topic}” go here.", size=20)
    slide.notes = f"Remember to demo {topic.lower()}."

print(f"Done — the deck now has {p.count} slides.")
