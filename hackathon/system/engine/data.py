"""Loading an institution folder and parsing its documents."""
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime

MONEY_RE = re.compile(r"£\s?([\d,]+(?:\.\d{1,2})?)")
LONG_DATE_RE = re.compile(
    r"\b(\d{1,2}) (January|February|March|April|May|June|July|August|September|"
    r"October|November|December) (\d{4})\b")
MONTH_YEAR_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December) (\d{4})\b")
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def money_values(text):
    out = []
    for m in MONEY_RE.finditer(text or ""):
        try:
            out.append(round(float(m.group(1).replace(",", "")), 2))
        except ValueError:
            pass
    return out


def parse_long_date(s):
    m = LONG_DATE_RE.search(s or "")
    if not m:
        return None
    return date(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1)))


def iso(d):
    return d.isoformat() if d else None


def to_date(s):
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return parse_long_date(s)


def fmt_gbp(x):
    return "£{:,.2f}".format(x)


def fmt_date(d):
    d = to_date(d) if not isinstance(d, date) else d
    if not d:
        return ""
    return "{} {} {}".format(d.day, MONTHS[d.month - 1], d.year)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def close_amount(a, b, tol=0.011):
    return a is not None and b is not None and abs(a - b) <= tol


class Doc:
    def __init__(self, meta, text):
        self.id = meta["doc_id"]
        self.type = meta.get("type", "")
        self.title = meta.get("title", "")
        self.dated = to_date(meta.get("dated"))
        self.text = text or ""
        self.fields = {}
        self.tables = []
        self._parse()

    def _parse(self):
        rows = []
        for line in self.text.splitlines():
            m = re.match(r"\s*\*\*([^*]+?):\*\*\s*(.*?)\s*$", line)
            if m:
                self.fields.setdefault(m.group(1).strip().lower(), m.group(2).strip())
            if line.strip().startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue
                rows.append(cells)
            elif rows:
                self.tables.append(rows)
                rows = []
        if rows:
            self.tables.append(rows)

    def field(self, *names):
        for n in names:
            v = self.fields.get(n.lower())
            if v:
                return v
        return None

    def table_dicts(self):
        """Each table as a list of dicts keyed by its header row."""
        out = []
        for t in self.tables:
            if len(t) < 2:
                continue
            head = [h.lower() for h in t[0]]
            out.append([dict(zip(head, r)) for r in t[1:]])
        return out

    @property
    def amounts(self):
        return money_values(self.text)

    @property
    def low(self):
        return self.text.lower()


class Institution:
    def __init__(self, folder):
        self.folder = folder
        self.name = os.path.basename(os.path.normpath(folder))
        self.cases = self._jsonl("cases.jsonl")
        self.alerts = {a["alert_id"]: a for a in self._jsonl("alerts.jsonl")}
        self.customers = {c["customer_id"]: c for c in self._jsonl("customers.jsonl")}
        self.tx = {}
        self.tx_by_cust = defaultdict(list)
        for t in self._jsonl("transactions.jsonl"):
            self.tx[t["transaction_id"]] = t
            self.tx_by_cust[t["customer_id"]].append(t)
        for lst in self.tx_by_cust.values():
            lst.sort(key=lambda t: (t.get("booked_at") or "", t["transaction_id"]))
        self.docs_by_cust = defaultdict(list)
        for meta in self._jsonl("evidence.jsonl"):
            path = os.path.join(folder, meta.get("file") or ("evidence/%s.md" % meta["doc_id"]))
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                text = ""
            self.docs_by_cust[meta["customer_id"]].append(Doc(meta, text))
        for lst in self.docs_by_cust.values():
            lst.sort(key=lambda d: d.id)
        self.reviews_by_cust = defaultdict(list)
        for r in self._jsonl("previous_reviews.jsonl"):
            self.reviews_by_cust[r["customer_id"]].append(r)
        self.internal_list = {e["entry_id"]: e for e in self._jsonl("internal_list.jsonl")}
        self.policy = self._read("POLICY.md")

    def _read(self, name):
        try:
            with open(os.path.join(self.folder, name), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def _jsonl(self, name):
        out = []
        path = os.path.join(self.folder, name)
        if not os.path.exists(path):
            return out
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        return out
