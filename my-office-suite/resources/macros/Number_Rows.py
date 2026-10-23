# Number Rows
# Inserts a running number (1, 2, 3 ...) in column A for every filled row of column B.
s = app.sheet
n = 0
for r in range(1, s.max_row + 1):
    if s.cell(r, 2) not in (None, ""):
        n += 1
        s.set_cell(r, 1, n)
print(f"Numbered {n} rows")
