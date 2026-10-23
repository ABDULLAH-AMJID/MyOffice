# Monthly Sales Report
# Creates a new sheet with sample monthly data, totals, a growth column and formatting.
import random
s = app.add_sheet("Report")
s["A1"] = "Month"; s["B1"] = "Sales"; s["C1"] = "Costs"; s["D1"] = "Profit"; s["E1"] = "Margin"
months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
random.seed(42)
for i, m in enumerate(months, start=2):
    sales = random.randint(80, 160) * 1000
    costs = int(sales * random.uniform(0.55, 0.8))
    s[f"A{i}"] = m
    s[f"B{i}"] = sales
    s[f"C{i}"] = costs
    s[f"D{i}"] = f"=B{i}-C{i}"
    s[f"E{i}"] = f"=D{i}/B{i}"
s["A14"] = "Total"
for c in "BCD":
    s[f"{c}14"] = f"=SUM({c}2:{c}13)"
s["E14"] = "=D14/B14"
s.format("A1:E1", bold=True, fill="#1f4e79", color="#ffffff", halign="center")
s.format("B2:D14", numfmt="#,##0")
s.format("E2:E14", numfmt="0.0%")
s.format("A14:E14", bold=True)
print("Report created on sheet 'Report'")
