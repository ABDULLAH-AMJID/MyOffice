"""Put the same picture in the top-right corner of every slide.

The first time the script reads a file outside the presentation's folder you
are asked for permission — that is the only way a script can touch files.
If anything fails, the whole run is rolled back and the deck stays as it was.
"""
p = app.presentation
image = app.input("Path to the picture", "")

if not image:
    print("No picture chosen.")
else:
    size = app.emu(90)                       # 90 px square
    margin = app.emu(30)
    x = p.width - size - margin
    try:
        for slide in p.slides:
            slide.shapes.add_picture(image, x, margin, w=size, h=size, name="Logo")
    except Exception as exc:                 # nothing was changed — the run rolls back
        app.message(f"Could not add the picture:\n{exc}")
    else:
        print(f"Added the picture to {p.count} slide(s).")
