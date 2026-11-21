"""Everything the rules need to know about one case."""
import re
from collections import defaultdict

from data import close_amount, norm, parse_long_date, to_date

STOP = {"ltd", "limited", "llp", "plc", "co", "the", "and", "of", "a", "for", "to", "at",
        "in", "on", "from", "with", "uk", "sells", "runs", "buys", "imports", "supplies",
        "overseas", "services", "firms", "stock", "manufacturers", "suppliers", "sites"}


class Decision:
    def __init__(self, verdict, code, reason, evidence, tag=""):
        self.verdict = verdict
        self.code = code
        self.reason = reason
        self.evidence = evidence
        self.tag = tag  # which rule decided; for debugging only


class Ctx:
    def __init__(self, inst, case):
        self.inst = inst
        self.case = case
        self.cust = inst.customers.get(case.get("customer_id"), {}) or {}
        self.cust_id = case.get("customer_id")
        self.name = self.cust.get("name") or ""
        self.alerts = [inst.alerts[a] for a in case.get("alert_ids", []) if a in inst.alerts]
        self.rules = {a.get("rule_id") for a in self.alerts}
        self.by_rule = defaultdict(list)
        for a in self.alerts:
            self.by_rule[a.get("rule_id")].append(a)
        ids = []
        for a in self.alerts:
            for t in a.get("transaction_ids") or []:
                if t not in ids:
                    ids.append(t)
        if case.get("held_payment_id") and case["held_payment_id"] not in ids:
            ids.append(case["held_payment_id"])
        self.atx = sorted([inst.tx[t] for t in ids if t in inst.tx],
                          key=lambda t: (t.get("booked_at") or "", t["transaction_id"]))
        self.held = inst.tx.get(case.get("held_payment_id")) if case.get("held_payment_id") else None
        self.txs = inst.tx_by_cust.get(self.cust_id, [])
        self.docs = inst.docs_by_cust.get(self.cust_id, [])
        self.reviews = inst.reviews_by_cust.get(self.cust_id, [])
        rw = case.get("review_window") or {}
        self.win_from = to_date(rw.get("from"))
        self.win_to = to_date(rw.get("to"))
        self.declared = self.cust.get("declared") or {}
        self.business = self.cust.get("business_activity") or ""
        self.is_business = self.cust.get("kind") == "business"

    # ---- documents -------------------------------------------------------
    def docs_of(self, *types):
        return [d for d in self.docs if d.type in types]

    def doc_with(self, *words, types=None):
        out = []
        for d in self.docs:
            if types and d.type not in types:
                continue
            if any(w in d.low for w in words):
                out.append(d)
        return out

    def chats(self):
        return self.docs_of("support_chat")

    def customer_lines(self, doc):
        return [m.group(1).strip() for m in
                re.finditer(r"\*\*Customer:\*\*\s*(.+)", doc.text)]

    def chat_text(self):
        return " ".join(" ".join(self.customer_lines(d)) for d in self.chats()).lower()

    def own_register(self):
        """The customer's own company register extract (business customers)."""
        for d in self.docs_of("company_register_extract"):
            comp = d.field("company")
            if comp and norm(comp) == norm(self.name):
                return d
        return None

    def controllers(self):
        """Names of the customer's directors and owners."""
        names = set()
        reg = self.own_register()
        if reg:
            for t in reg.table_dicts():
                for r in t:
                    n = r.get("officer") or r.get("person with significant control") or r.get("person")
                    if n:
                        names.add(n.strip())
        return names

    # ---- transactions ----------------------------------------------------
    def a_in(self):
        return [t for t in self.atx if t.get("direction") == "in"]

    def a_out(self):
        return [t for t in self.atx if t.get("direction") == "out"]

    def big(self, txs, n=1):
        return sorted(txs, key=lambda t: -(t.get("amount_gbp") or 0))[:n]

    def tm01_multiple(self):
        for a in self.by_rule.get("TM01", []):
            m = (a.get("measured") or {}).get("multiple")
            if m is not None:
                return float(m)
        return None

    def is_own_name(self, cp):
        return bool(cp) and norm(cp) == norm(self.name)

    def find_tx_amount(self, amount, direction=None, txs=None):
        for t in (txs if txs is not None else self.txs):
            if direction and t.get("direction") != direction:
                continue
            if close_amount(t.get("amount_gbp"), amount):
                return t
        return None

    def high_risk_countries(self):
        m = re.search(r"Higher-risk countries[^:]*:\s*(.+)", self.inst.policy)
        if not m:
            return set()
        return {c.strip().rstrip(".").lower() for c in m.group(1).split(",")}

    def doc_date(self, doc):
        return doc.dated or parse_long_date(doc.text)


def words(s):
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if w not in STOP and len(w) > 2}
