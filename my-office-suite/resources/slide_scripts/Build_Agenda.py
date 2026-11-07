"""Build an agenda slide from the titles of the other slides."""
p = app.presentation

# A slide that still shows its layout prompt has no real title yet.
PROMPTS = ("", "Click to add title", "Click to add text")

titles = [t.strip() for t in (s.title for s in p.slides) if t.strip() not in PROMPTS]

agenda = p.add_slide(layout="title_content", title="Agenda", body="\n".join(titles[:12]), index=1)
agenda.transition = "fade"
agenda.notes = "Generated from the slide titles."

print(f"Agenda created with {min(len(titles), 12)} item(s).")
