"""Add a table and a chart to a new slide."""
p = app.presentation

s = p.add_slide(layout="title_only", title="Numbers")

s.shapes.add_table([["Quarter", "Sales", "Target"],
                    ["Q1", "120", "100"],
                    ["Q2", "150", "140"],
                    ["Q3", "138", "150"]],
                   x=app.emu(60), y=app.emu(150), w=app.emu(420), h=app.emu(200))

s.shapes.add_chart("column", ["Q1", "Q2", "Q3"],
                   [["Sales", [120, 150, 138]], ["Target", [100, 140, 150]]],
                   title="Sales vs target",
                   x=app.emu(510), y=app.emu(150), w=app.emu(420), h=app.emu(300))

print("Table and chart added.")
