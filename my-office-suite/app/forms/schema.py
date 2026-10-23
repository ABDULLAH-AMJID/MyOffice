"""Forms / Survey model: fields <-> JSON Schema (draft 2020-12), validation, HTML export, response storage."""
from __future__ import annotations

import html
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.services.paths import user_subdir

KINDS = ["text", "longtext", "number", "integer", "email", "date", "choice", "multichoice", "checkbox", "rating"]


@dataclass
class FormField:
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    options: list = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    pattern: str = ""
    help: str = ""
    placeholder: str = ""


@dataclass
class Form:
    title: str
    description: str = ""
    fields: list = field(default_factory=list)
    id: str = ""
    submit_text: str = "Submit"

    def __post_init__(self):
        if not self.id:
            self.id = re.sub(r"[^\w\-]+", "_", self.title.strip().lower())[:40] or "form"


def slug_key(label: str, existing: set) -> str:
    base = re.sub(r"[^\w]+", "_", label.strip().lower()).strip("_")[:30] or "field"
    if base[0].isdigit():
        base = "f_" + base
    key, n = base, 2
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


def field_schema(f: FormField) -> dict:
    s: dict = {"title": f.label}
    if f.help:
        s["description"] = f.help
    if f.kind in ("text", "longtext"):
        s["type"] = "string"
        if f.pattern:
            s["pattern"] = f.pattern
        if f.minimum is not None:
            s["minLength"] = int(f.minimum)
        if f.maximum is not None:
            s["maxLength"] = int(f.maximum)
    elif f.kind == "email":
        s.update(type="string", format="email", pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    elif f.kind == "date":
        s.update(type="string", format="date", pattern=r"^\d{4}-\d{2}-\d{2}$")
    elif f.kind in ("number", "integer", "rating"):
        s["type"] = "integer" if f.kind in ("integer", "rating") else "number"
        lo = 1 if f.kind == "rating" and f.minimum is None else f.minimum
        hi = 5 if f.kind == "rating" and f.maximum is None else f.maximum
        if lo is not None:
            s["minimum"] = lo
        if hi is not None:
            s["maximum"] = hi
    elif f.kind == "choice":
        s.update(type="string", enum=list(f.options))
    elif f.kind == "multichoice":
        s.update(type="array", items={"type": "string", "enum": list(f.options)}, uniqueItems=True)
        if f.required:
            s["minItems"] = 1
    elif f.kind == "checkbox":
        s["type"] = "boolean"
        if f.required:
            s["const"] = True
    s["x-kind"] = f.kind
    return s


def to_json_schema(form: Form) -> dict:
    props = {f.key: field_schema(f) for f in form.fields}
    req = [f.key for f in form.fields if f.required and f.kind != "multichoice"]
    req += [f.key for f in form.fields if f.required and f.kind == "multichoice"]
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"urn:myoffice:form:{form.id}",
            "title": form.title, "description": form.description, "type": "object", "properties": props,
            "required": req, "additionalProperties": False, "x-submit": form.submit_text,
            "x-order": [f.key for f in form.fields]}


def from_json_schema(schema: dict) -> Form:
    form = Form(schema.get("title", "Form"), schema.get("description", ""), [], submit_text=schema.get("x-submit", "Submit"))
    props = schema.get("properties", {})
    order = schema.get("x-order") or list(props)
    req = set(schema.get("required", []))
    for key in order:
        p = props.get(key, {})
        kind = p.get("x-kind")
        if not kind:
            t = p.get("type")
            kind = ("choice" if "enum" in p else "email" if p.get("format") == "email" else
                    "date" if p.get("format") == "date" else "number" if t == "number" else
                    "integer" if t == "integer" else "checkbox" if t == "boolean" else
                    "multichoice" if t == "array" else "text")
        opts = p.get("enum") or p.get("items", {}).get("enum") or []
        lo = p.get("minimum", p.get("minLength"))
        hi = p.get("maximum", p.get("maxLength"))
        form.fields.append(FormField(key, p.get("title", key), kind, key in req, list(opts), lo, hi,
                                     p.get("pattern", "") if kind in ("text", "longtext") else "", p.get("description", "")))
    return form


def validate(form: Form, response: dict) -> list[str]:
    """Return a list of human readable validation errors (empty = valid)."""
    import jsonschema

    schema = to_json_schema(form)
    cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in response.items()}
    cleaned = {k: v for k, v in cleaned.items() if v not in (None, "", [])}
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    errors: list[str] = []
    labels = {f.key: f.label for f in form.fields}
    kinds = {f.key: f.kind for f in form.fields}
    for err in sorted(validator.iter_errors(cleaned), key=lambda e: list(e.path)):
        key = err.path[0] if err.path else ""
        where = labels.get(key, key)
        if err.validator == "required":
            missing = re.findall(r"'([^']+)' is a required property", err.message)
            where = labels.get(missing[0], missing[0]) if missing else ""
            msg = f"{where}: this field is required"
        elif err.validator == "const":
            msg = f"{where}: must be checked"
        elif kinds.get(key) == "email" and err.validator in ("format", "pattern"):
            msg = f"{where}: enter a valid e-mail address"
        elif kinds.get(key) == "date" and err.validator in ("format", "pattern"):
            msg = f"{where}: enter a date as YYYY-MM-DD"
        elif err.validator == "pattern":
            msg = f"{where}: the answer does not have the expected format"
        elif err.validator == "enum":
            msg = f"{where}: choose one of the listed options"
        else:
            msg = f"{where}: {err.message}"
        if msg not in errors:
            errors.append(msg)
    return errors


# ============================================================================= storage
def forms_dir() -> Path:
    return user_subdir("forms")


def save_form(form: Form) -> Path:
    path = forms_dir() / f"{form.id}.schema.json"
    path.write_text(json.dumps(to_json_schema(form), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_form(path: str | Path) -> Form:
    return from_json_schema(json.loads(Path(path).read_text(encoding="utf-8")))


def list_forms() -> list[Path]:
    return sorted(forms_dir().glob("*.schema.json"))


def responses_path(form: Form) -> Path:
    return forms_dir() / f"{form.id}.responses.jsonl"


def store_response(form: Form, response: dict) -> dict:
    rec = dict(response)
    rec["_submitted"] = datetime.now().isoformat(timespec="seconds")
    with open(responses_path(form), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_responses(form: Form) -> list[dict]:
    p = responses_path(form)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def import_responses(form: Form, path: str) -> tuple[int, list[str]]:
    """Import responses exported by the HTML form (JSON object, list, or JSON lines)."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if text.startswith("["):
        items = json.loads(text)
    elif text.startswith("{") and "\n" not in text:
        items = [json.loads(text)]
    else:
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    ok, problems = 0, []
    for i, item in enumerate(items, start=1):
        data = {k: v for k, v in item.items() if not k.startswith("_")}
        errs = validate(form, data)
        if errs:
            problems.append(f"#{i}: " + "; ".join(errs))
            continue
        store_response(form, data)
        ok += 1
    return ok, problems


def response_rows(form: Form, responses: list[dict]) -> list[list]:
    header = ["Submitted"] + [f.label for f in form.fields]
    rows = [header]
    for r in responses:
        row = [r.get("_submitted", "")]
        for f in form.fields:
            v = r.get(f.key)
            if isinstance(v, list):
                v = ", ".join(map(str, v))
            elif isinstance(v, bool):
                v = "Yes" if v else "No"
            row.append(v)
        rows.append(row)
    return rows


def export_html(form: Form, path: str) -> str:
    """Standalone offline survey page: validates in the browser and downloads the answers as JSON."""
    def inp(f: FormField) -> str:
        req = " required" if f.required else ""
        name = html.escape(f.key)
        if f.kind == "longtext":
            return f"<textarea name='{name}' rows='4'{req}></textarea>"
        if f.kind in ("choice",):
            opts = "".join(f"<option>{html.escape(str(o))}</option>" for o in f.options)
            return f"<select name='{name}'{req}><option value=''></option>{opts}</select>"
        if f.kind == "multichoice":
            return "".join(f"<label class='opt'><input type='checkbox' name='{name}' value='{html.escape(str(o))}'> "
                           f"{html.escape(str(o))}</label>" for o in f.options)
        if f.kind == "checkbox":
            return f"<label class='opt'><input type='checkbox' name='{name}' value='true'{req}> {html.escape(f.help or 'Yes')}</label>"
        if f.kind == "rating":
            lo, hi = int(f.minimum or 1), int(f.maximum or 5)
            return "".join(f"<label class='opt'><input type='radio' name='{name}' value='{i}'{req}> {i}</label>"
                           for i in range(lo, hi + 1))
        t = {"number": "number", "integer": "number", "email": "email", "date": "date"}.get(f.kind, "text")
        extra = ""
        if f.minimum is not None and f.kind in ("number", "integer"):
            extra += f" min='{f.minimum}'"
        if f.maximum is not None and f.kind in ("number", "integer"):
            extra += f" max='{f.maximum}'"
        if f.kind == "integer":
            extra += " step='1'"
        if f.kind == "number":
            extra += " step='any'"
        if f.pattern and f.kind == "text":
            extra += f" pattern='{html.escape(f.pattern)}'"
        return f"<input type='{t}' name='{name}'{extra}{req} placeholder='{html.escape(f.placeholder)}'>"

    rows = "".join(f"<div class='field'><label class='lbl'>{html.escape(f.label)}{' *' if f.required else ''}</label>"
                   f"{inp(f)}{'<small>' + html.escape(f.help) + '</small>' if f.help and f.kind != 'checkbox' else ''}</div>"
                   for f in form.fields)
    kinds = json.dumps({f.key: f.kind for f in form.fields})
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(form.title)}</title>
<style>body{{font-family:Segoe UI,Arial,'Noto Nastaliq Urdu',sans-serif;background:#f3f4f6;margin:0;padding:24px}}
.card{{max-width:640px;margin:auto;background:#fff;border-radius:12px;padding:28px;box-shadow:0 2px 10px #0001}}
h1{{margin-top:0;color:#1f3864}}.field{{margin:16px 0}}.lbl{{display:block;font-weight:600;margin-bottom:6px}}
input[type=text],input[type=email],input[type=number],input[type=date],select,textarea{{width:100%;padding:8px;border:1px solid #ccd;border-radius:6px;box-sizing:border-box}}
.opt{{margin-right:14px}}small{{color:#667}}button{{background:#2b6be6;color:#fff;border:0;border-radius:6px;padding:10px 22px;font-size:15px}}</style></head>
<body><div class="card"><h1>{html.escape(form.title)}</h1><p>{html.escape(form.description)}</p>
<form id="f">{rows}<button type="submit">{html.escape(form.submit_text)}</button></form>
<p id="done" style="display:none">Thank you! Your answers were downloaded as a JSON file \u2014 send it to the organiser.</p></div>
<script>const kinds={kinds};
document.getElementById('f').addEventListener('submit',e=>{{e.preventDefault();const fd=new FormData(e.target);const out={{}};
for(const k in kinds){{const v=fd.getAll(k);const kind=kinds[k];
if(kind==='multichoice')out[k]=v;else if(kind==='checkbox')out[k]=v.length>0;
else if(['number','integer','rating'].includes(kind))out[k]=v.length&&v[0]!==''?Number(v[0]):null;else out[k]=v.length?v[0]:'';}}
const blob=new Blob([JSON.stringify(out)],{{type:'application/json'}});const a=document.createElement('a');
a.href=URL.createObjectURL(blob);a.download='{form.id}_response_'+Date.now()+'.json';a.click();
document.getElementById('f').style.display='none';document.getElementById('done').style.display='block';}});</script></body></html>"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(page)
    os.replace(tmp, path)
    return path


def sample_form() -> Form:
    return Form("Customer Feedback", "Tell us about your experience.", [
        FormField("name", "Your name", "text", True, placeholder="Full name"),
        FormField("email", "E-mail", "email", False),
        FormField("visit_date", "Date of visit", "date", False),
        FormField("rating", "Overall rating", "rating", True, minimum=1, maximum=5),
        FormField("services", "Services used", "multichoice", False, ["Writer", "Spreadsheets", "Presentation", "PDF"]),
        FormField("city", "City", "choice", False, ["Sahiwal", "Lahore", "Karachi", "Islamabad", "Other"]),
        FormField("comments", "Comments", "longtext", False),
        FormField("consent", "Consent", "checkbox", True, help="I agree that my answers are stored"),
    ])


def form_to_dict(form: Form) -> dict:
    return asdict(form)
