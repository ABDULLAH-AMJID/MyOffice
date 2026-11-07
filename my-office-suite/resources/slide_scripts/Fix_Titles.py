"""Tidy the title of every slide: fix spacing and capitalisation."""
p = app.presentation

changed = 0
for slide in p.slides:
    for shape in slide.shapes:
        if shape.placeholder != "title":
            continue
        old = shape.text
        new = " ".join(old.split())                 # collapse double spaces
        if new and new == new.lower():
            new = new[:1].upper() + new[1:]         # sentence case
        if new != old:
            shape.text = new                        # keeps size, colour and font
            changed += 1

print(f"Tidied {changed} title(s) across {p.count} slide(s).")
