"""Plant each edge case into a copy of a real CLOSE case and check the system no
longer closes it. Also checks untouched controls still close.

Usage (from the hackathon folder):  python3 system/tests/test_traps.py institutions
Writes nothing outside a temporary folder.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "..", "engine", "main.py")


def rows(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def write(path, items):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(x) for x in items) + "\n")


def run(folder, out_dir):
    out = os.path.join(out_dir, "%s_out.jsonl" % os.path.basename(os.path.normpath(folder)))
    env = dict(os.environ, TM_DEBUG="1", PYTHONHASHSEED="0")
    subprocess.run([sys.executable, "-B", MAIN, folder, out], check=True, env=env)
    return {r["case_id"]: r for r in rows(out)}


class Copy:
    """A copy of an institution folder we can edit."""

    def __init__(self, src, tmp):
        self.dir = os.path.join(tmp, os.path.basename(src))
        shutil.copytree(src, self.dir)
        self.f = {n: rows(os.path.join(self.dir, n + ".jsonl")) for n in
                  ("cases", "alerts", "customers", "transactions", "evidence", "previous_reviews")}
        self.tx = {t["transaction_id"]: t for t in self.f["transactions"]}
        self.alert = {a["alert_id"]: a for a in self.f["alerts"]}
        self.case = {c["case_id"]: c for c in self.f["cases"]}
        self.keep = set()

    def cust(self, cid):
        return self.case[cid]["customer_id"]

    def txs(self, cid):
        return [t for t in self.f["transactions"] if t["customer_id"] == self.cust(cid)]

    def alerted(self, cid):
        ids = {t for a in self.case[cid]["alert_ids"] for t in self.alert[a]["transaction_ids"]}
        return [self.tx[i] for i in ids if i in self.tx]

    def add_tx(self, cid, like, n, **kw):
        t = dict(like)
        t.update(kw)
        t["transaction_id"] = "%s-TRAP%03d" % (like["transaction_id"][:2], n)
        self.f["transactions"].append(t)
        return t

    def doc(self, cid, dtype):
        return [e for e in self.f["evidence"] if e["customer_id"] == self.cust(cid) and e["type"] == dtype][0]

    def edit_doc(self, meta, old, new):
        p = os.path.join(self.dir, meta["file"])
        with open(p, encoding="utf-8") as fh:
            s = fh.read()
        assert old in s, (meta["doc_id"], old)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(s.replace(old, new, 1))

    def save(self):
        self.f["cases"] = [c for c in self.f["cases"] if c["case_id"] in self.keep]
        for n, items in self.f.items():
            write(os.path.join(self.dir, n + ".jsonl"), items)


def shift(iso, days):
    from datetime import datetime, timedelta
    return (datetime.strptime(iso[:10], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d") + iso[10:]


def pick(tags, tag, n=1, skip=0):
    ids = sorted(k for k, v in tags.items() if v["verdict"] == "CLOSE" and v["_tag"].split("|")[0] == tag)
    return ids[skip:skip + n]


def main(base):
    results = []
    tmp = tempfile.mkdtemp()
    try:
        for inst in ("bank", "payments_fx", "investment"):
            src = os.path.join(base, inst)
            before = run_copy_baseline(src, tmp)
            C = Copy(src, os.path.join(tmp, "edit"))
            tests = PLANT[inst](C, before)
            C.save()
            after = run(C.dir, tmp)
            for cid, label, want in tests:
                got = after[cid]["verdict"]
                ok = got in want
                results.append((ok, inst, label, cid, before[cid]["verdict"], got, after[cid]["reason_code"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    width = max(len(r[2]) for r in results)
    for ok, inst, label, cid, b, g, code in results:
        print("%s  %-11s %-*s %s  %s -> %s (%s)" % ("PASS" if ok else "FAIL", inst, width, label, cid, b, g, code))
    failed = [r for r in results if not r[0]]
    print("\n%d of %d passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


_BASE = {}


def run_copy_baseline(src, tmp):
    if src not in _BASE:
        _BASE[src] = run(src, tmp)
    return _BASE[src]


# ---------------------------------------------------------------------------
def plant_bank(C, tags):
    t = []
    # 1. inheritance arrives and is sent straight on to a new payee
    cid = pick(tags, "event:solicitor_letter")[0]
    inn = [x for x in C.alerted(cid) if x["direction"] == "in" and "Solicitors" in (x["counterparty_name"] or "")][0]
    C.add_tx(cid, inn, 1, direction="out", channel="faster_payment", booked_at=shift(inn["booked_at"], 1),
             amount_gbp=round(inn["amount_gbp"] * 0.9, 2), amount=round(inn["amount_gbp"] * 0.9, 2),
             counterparty_name="Kestrel Global Holdings Ltd", counterparty_country="United Arab Emirates",
             counterparty_account="99-11-22 33445566", reference="INVESTMENT")
    t.append((cid, "inheritance sent straight on", {"ESCALATE"}))
    # 2. insurance payout from a company the letter does not name
    cid = pick(tags, "event:insurer_letter")[0]
    inn = [x for x in C.alerted(cid) if x["direction"] == "in"][0]
    inn["counterparty_name"] = "Northgate Holdings Ltd"
    t.append((cid, "payout from unnamed payer", {"RFI", "ESCALATE"}))
    # 3. insurer letter dated two months after the money
    cid = pick(tags, "event:insurer_letter", skip=1)[0]
    inn = [x for x in C.alerted(cid) if x["direction"] == "in"][0]
    C.doc(cid, "insurer_letter")["dated"] = shift(inn["booked_at"], 60)[:10]
    t.append((cid, "letter dated after the money", {"RFI", "ESCALATE"}))
    # 4. a second big cash deposit before the 'one-off' car sale
    cid = pick(tags, "event:vehicle_sale_receipt")[0]
    dep = [x for x in C.alerted(cid) if x["channel"] == "cash_deposit"][0]
    C.add_tx(cid, dep, 2, booked_at=shift(dep["booked_at"], -40))
    t.append((cid, "repeated one-off cash sale", {"RFI", "ESCALATE"}))
    # 5. returned cash paid in at a different branch
    cid = pick(tags, "cash_return")[0]
    dep = [x for x in C.alerted(cid) if x["channel"] == "cash_deposit"][0]
    dep["location"] = "Faraway Road branch"
    t.append((cid, "cash back at another branch", {"RFI", "ESCALATE"}))
    # 6. returned cash with no earlier review of the withdrawal
    cid = pick(tags, "cash_return", skip=1)[0]
    C.f["previous_reviews"] = [r for r in C.f["previous_reviews"] if r["customer_id"] != C.cust(cid)]
    t.append((cid, "cash back, no earlier review", {"RFI", "ESCALATE"}))
    # 7-10. hospital payment traps
    hc = pick(tags, "hospital", n=4)
    pay = lambda cid: [x for x in C.alerted(cid) if x["direction"] == "out"][0]
    pay(hc[0])["counterparty_name"] = "Cedar Trading SARL"
    t.append((hc[0], "hospital paid to someone else", {"ESCALATE"}))
    p = pay(hc[1])
    C.add_tx(hc[1], p, 3, booked_at=shift(p["booked_at"], -50))
    t.append((hc[1], "repeated hospital payments", {"ESCALATE"}))
    p = pay(hc[2])
    C.add_tx(hc[2], p, 4, direction="in", channel="faster_payment", booked_at=shift(p["booked_at"], -3),
             counterparty_name="Riley Stanmore", counterparty_country="United Kingdom", reference="FOR YOU")
    t.append((hc[2], "hospital bill funded by a stranger", {"ESCALATE"}))
    inv = C.doc(hc[3], "hospital_invoice")
    pat = [l for l in open(os.path.join(C.dir, inv["file"]), encoding="utf-8").read().splitlines()
           if l.startswith("**Patient:**")][0]
    C.edit_doc(inv, pat, "**Patient:** Morgan Quillfeather")
    t.append((hc[3], "patient not family", {"RFI", "ESCALATE"}))
    # controls: untouched
    for tag in ("event:solicitor_letter", "hospital", "takings"):
        cid = pick(tags, tag, skip=5)[0]
        t.append((cid, "control (untouched) " + tag, {"CLOSE"}))
    C.keep |= {x[0] for x in t}
    return t


def plant_px(C, tags):
    t = []
    # an alerted £10k+ payment to a supplier the client has never paid before
    for i, tag in enumerate(("group", "refund", "freight")):
        for cid in pick(tags, tag, n=45):
            if tags[cid]["reason"].count("paid regularly since"):
                break
        # the largest alerted payment named in the "paid regularly" sentence
        named = re.split(r"[Tt]he other alerted", tags[cid]["reason"])[1].split("went to")[0]
        target = sorted((x for x in C.alerted(cid) if x["direction"] == "out"
                         and x["counterparty_name"] in named), key=lambda x: -x["amount_gbp"])
        target[0]["counterparty_name"] = "Brand New Ventures %d Ltd" % i
        t.append((cid, "new £10k+ payee, no invoice (%s)" % tag, {"RFI", "ESCALATE"}))
    for tag in ("payment_file", "own_account"):
        t.append((pick(tags, tag, skip=3)[0], "control (untouched) " + tag, {"CLOSE"}))
    C.keep |= {x[0] for x in t}
    return t


def plant_iv(C, tags):
    t = []
    # house sale money withdrawn straight to a new third party (not invested)
    cid = pick(tags, "event:completion_statement")[0]
    inn = [x for x in C.alerted(cid) if x["direction"] == "in" and "Conveyancing" in x["counterparty_name"]][0]
    C.add_tx(cid, inn, 5, direction="out", channel="withdrawal", booked_at=shift(inn["booked_at"], 2),
             amount_gbp=round(inn["amount_gbp"] * 0.8, 2), amount=round(inn["amount_gbp"] * 0.8, 2),
             counterparty_name="Harbour Point Trading LLC", counterparty_country="United Arab Emirates",
             reference="TRANSFER")
    t.append((cid, "sale proceeds withdrawn to a stranger", {"ESCALATE"}))
    t.append((pick(tags, "event:completion_statement", skip=4)[0], "control: proceeds invested in a fund",
              {"CLOSE"}))
    C.keep |= {x[0] for x in t}
    return t


PLANT = {"bank": plant_bank, "payments_fx": plant_px, "investment": plant_iv}

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "institutions"))
