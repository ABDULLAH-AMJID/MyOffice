# Urdu Digits In Writer
# Replaces Western digits 0-9 with Urdu (Extended Arabic-Indic) digits in the active Writer document.
w = app.writer
table = {"0": "\u06f0", "1": "\u06f1", "2": "\u06f2", "3": "\u06f3", "4": "\u06f4",
         "5": "\u06f5", "6": "\u06f6", "7": "\u06f7", "8": "\u06f8", "9": "\u06f9"}
total = 0
for western, urdu in table.items():
    total += w.replace_all(western, urdu)
app.message(f"Replaced {total} digit(s).")
