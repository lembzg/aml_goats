#!/usr/bin/env python3
"""Analyst workbench for the alert triage system.

    python3 ui/serve.py                      # from the hackathon folder
    python3 ui/serve.py <institutions dir> <output dir> [--port 8000]

Opens a local web page showing every case's decision next to its alerts,
customer, transactions and documents. Read-only: it never changes the data
or the system's output. Standard library only.
"""
import argparse
import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INSTITUTION_NAMES = {"bank": "Bank", "emoney": "E-money firm", "investment": "Investment firm",
                     "payments_fx": "Payments and FX firm"}


def jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    return out


class Institution:
    def __init__(self, folder, output):
        self.key = os.path.basename(os.path.normpath(folder))
        self.folder = folder
        self.cases = {c["case_id"]: c for c in jsonl(os.path.join(folder, "cases.jsonl"))}
        self.order = [c["case_id"] for c in jsonl(os.path.join(folder, "cases.jsonl"))]
        self.alerts = {a["alert_id"]: a for a in jsonl(os.path.join(folder, "alerts.jsonl"))}
        self.customers = {c["customer_id"]: c for c in jsonl(os.path.join(folder, "customers.jsonl"))}
        self.tx_by_cust = {}
        self.tx = {}
        for t in jsonl(os.path.join(folder, "transactions.jsonl")):
            self.tx[t["transaction_id"]] = t
            self.tx_by_cust.setdefault(t["customer_id"], []).append(t)
        self.docs_by_cust = {}
        for d in jsonl(os.path.join(folder, "evidence.jsonl")):
            self.docs_by_cust.setdefault(d["customer_id"], []).append(d)
        self.reviews_by_cust = {}
        for r in jsonl(os.path.join(folder, "previous_reviews.jsonl")):
            self.reviews_by_cust.setdefault(r["customer_id"], []).append(r)
        self.listed = {e["entry_id"]: e for e in jsonl(os.path.join(folder, "internal_list.jsonl"))}
        self.decisions = {d["case_id"]: d for d in jsonl(output)} if output else {}

    def summary(self):
        rows = []
        for cid in self.order:
            c = self.cases[cid]
            d = self.decisions.get(cid, {})
            cu = self.customers.get(c.get("customer_id"), {})
            rows.append({
                "case_id": cid, "customer": cu.get("name", ""), "kind": cu.get("kind", ""),
                "rules": sorted({self.alerts[a]["rule_id"] for a in c.get("alert_ids", []) if a in self.alerts}),
                "verdict": d.get("verdict", "MISSING"), "reason_code": d.get("reason_code", ""),
                "opened": (c.get("opened_at") or "")[:10], "held": bool(c.get("held_payment_id")),
            })
        return rows

    def case(self, cid):
        c = self.cases.get(cid)
        if not c:
            return None
        cust_id = c.get("customer_id")
        d = self.decisions.get(cid)
        cited = set((d or {}).get("evidence", []))
        alerted = set()
        alerts = []
        for a in c.get("alert_ids", []):
            if a in self.alerts:
                alerts.append(self.alerts[a])
                alerted.update(self.alerts[a].get("transaction_ids") or [])
        frm = (c.get("review_window") or {}).get("from", "")
        txs = []
        for t in sorted(self.tx_by_cust.get(cust_id, []), key=lambda t: t.get("booked_at") or ""):
            tid = t["transaction_id"]
            in_window = (t.get("booked_at") or "")[:10] >= frm
            if tid in alerted or tid in cited or in_window or tid == c.get("held_payment_id"):
                txs.append(dict(t, alerted=tid in alerted, cited=tid in cited,
                                held=tid == c.get("held_payment_id"), before_window=not in_window))
        docs = []
        for meta in sorted(self.docs_by_cust.get(cust_id, []), key=lambda x: x["doc_id"]):
            path = os.path.join(self.folder, meta.get("file") or "evidence/%s.md" % meta["doc_id"])
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                text = ""
            docs.append(dict(meta, text=text, cited=meta["doc_id"] in cited))
        docs.sort(key=lambda x: (not x["cited"], x["type"] in ("due_diligence_summary", "proof_of_address",
                                                                "contact_note"), x["doc_id"]))
        reviews = [dict(r, cited=r["review_id"] in cited) for r in self.reviews_by_cust.get(cust_id, [])]
        listed_ids = set(cited) | {(a.get("measured") or {}).get("list_entry") for a in alerts}
        listed = [dict(self.listed[e], cited=e in cited) for e in listed_ids if e in self.listed]
        return {"case": c, "customer": self.customers.get(cust_id, {}), "alerts": alerts,
                "transactions": txs, "documents": docs, "reviews": reviews, "listed": listed,
                "decision": d, "institution": self.key}


def load_all(inst_dir, out_dir):
    out = {}
    for name in sorted(os.listdir(inst_dir)):
        folder = os.path.join(inst_dir, name)
        if os.path.isdir(folder) and os.path.exists(os.path.join(folder, "cases.jsonl")):
            out[name] = Institution(folder, os.path.join(out_dir, name + ".jsonl"))
            print("loaded %-12s %5d cases, %5d decisions" % (name, len(out[name].cases),
                                                            len(out[name].decisions)))
    return out


def make_handler(data):
    page = os.path.join(HERE, "index.html")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            raw = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def js(self, obj, code=200):
            self.send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/index.html"):
                with open(page, "rb") as fh:
                    return self.send(200, fh.read(), "text/html; charset=utf-8")
            if u.path == "/api/institutions":
                return self.js([{"key": k, "name": INSTITUTION_NAMES.get(k, k), "cases": len(v.cases),
                                 "decided": len(v.decisions)} for k, v in data.items()])
            if u.path == "/api/cases":
                inst = data.get(q.get("inst", ""))
                return self.js(inst.summary() if inst else [], 200 if inst else 404)
            if u.path == "/api/case":
                inst = data.get(q.get("inst", ""))
                res = inst.case(q.get("id", "")) if inst else None
                return self.js(res if res else {"error": "Case not found"}, 200 if res else 404)
            self.send(404, "Not found", "text/plain")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("institutions", nargs="?", default=os.path.join(ROOT, "institutions"))
    ap.add_argument("output", nargs="?", default=os.path.join(ROOT, "output"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    data = load_all(a.institutions, a.output)
    if not data:
        sys.exit("No institution folders found in %s" % a.institutions)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(data))
    url = "http://127.0.0.1:%d/" % a.port
    print("Analyst workbench running at %s  (Ctrl+C to stop)" % url)
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
