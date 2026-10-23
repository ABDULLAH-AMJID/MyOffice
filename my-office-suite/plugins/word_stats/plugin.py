"""Word Statistics add-in (example Python add-in for MyOffice)."""
import re
from collections import Counter

STOP = set("the a an and or of to in is are was were be been it this that for on with as by at from not".split())


def analyse(text: str) -> dict:
    words = re.findall(r"[\w'\u0600-\u06FF\u0900-\u097F]+", text)
    sentences = [s for s in re.split(r"[.!?\u06d4\u061f]+", text) if s.strip()]
    common = Counter(w.lower() for w in words if w.lower() not in STOP and len(w) > 2).most_common(5)
    n = len(words)
    return {"words": n, "sentences": len(sentences), "reading_min": n / 230.0, "speaking_min": n / 130.0,
            "avg_sentence": n / max(1, len(sentences)), "common": common}


def report(doc):
    s = analyse(doc.text())
    lines = [f"Words: {s['words']}", f"Sentences: {s['sentences']}",
             f"Average sentence length: {s['avg_sentence']:.1f} words",
             f"Reading time: {s['reading_min']:.1f} min", f"Speaking time: {s['speaking_min']:.1f} min"]
    if s["common"]:
        lines.append("Most frequent: " + ", ".join(f"{w} ({c})" for w, c in s["common"]))
    return "\n".join(lines)


def register(api):
    api.add_ribbon_action("writer", "Word Statistics", lambda doc: api.show_message("Word Statistics", report(doc)),
                          icon="word_count", tooltip="Reading time and word frequency")
    api.log("registered")


def unregister(api):
    api.log("unregistered")
