# Format Table Header
# Makes the first row of the used range bold with a blue fill and adds a SUM row below numeric columns.
s = app.sheet
last_row, last_col = s.max_row, s.max_col
if last_row == 0:
    app.message("The sheet is empty.")
else:
    from string import ascii_uppercase
    def col(n):
        name = ""
        while n:
            n, r = divmod(n - 1, 26)
            name = ascii_uppercase[r] + name
        return name
    s.format(f"A1:{col(last_col)}1", bold=True, fill="#1f4e79", color="#ffffff", halign="center")
    total_row = last_row + 1
    s[f"A{total_row}"] = "Total"
    for c in range(2, last_col + 1):
        values = [s.cell(r, c) for r in range(2, last_row + 1)]
        if any(isinstance(v, float) for v in values):
            s[f"{col(c)}{total_row}"] = f"=SUM({col(c)}2:{col(c)}{last_row})"
    s.format(f"A{total_row}:{col(last_col)}{total_row}", bold=True, numfmt="#,##0.00")
    print(f"Formatted header and added totals in row {total_row}")
