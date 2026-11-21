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
                  ("cases", "alerts", "customers", "transactions", "evidence", "previous_reviews",
                   "internal_list")}
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

    def customer(self, cid):
        return [c for c in self.f["customers"] if c["customer_id"] == self.cust(cid)][0]

    def add_alert(self, cid, rule_id, tx_ids, measured=None, first=False, n=1):
        case = self.case[cid]
        a = {"alert_id": "%s-ALTRAP%02d" % (cid[:2], n), "case_id": cid, "customer_id": self.cust(cid),
             "rule_name": RULE_NAMES.get(rule_id, rule_id), "rule_id": rule_id,
             "created_at": case.get("opened_at"), "message": "Planted %s alert." % rule_id,
             "measured": measured or {}, "transaction_ids": list(tx_ids)}
        self.f["alerts"].append(a)
        self.alert[a["alert_id"]] = a
        case["alert_ids"] = ([a["alert_id"]] + case["alert_ids"]) if first else (case["alert_ids"] + [a["alert_id"]])
        return a

    def append_to_alert(self, cid, tx_id):
        self.alert[self.case[cid]["alert_ids"][0]]["transaction_ids"].append(tx_id)

    def new_tx(self, cid, n, **kw):
        like = self.alerted(cid)[0]
        t = dict(like)
        t.update(kw)
        t["transaction_id"] = "%s-TRAP%03d" % (cid[:2], n)
        t["amount"] = t.get("amount_gbp")
        t["currency"] = "GBP"
        self.f["transactions"].append(t)
        self.tx[t["transaction_id"]] = t
        return t

    def add_doc(self, cid, doc_id, dtype, title, dated, body):
        rel = "evidence/%s.md" % doc_id
        with open(os.path.join(self.dir, rel), "w", encoding="utf-8") as fh:
            fh.write("# %s\n\n**Document ID:** %s\n**Dated:** %s\n\n%s\n" % (title, doc_id, long_date(dated), body))
        meta = {"doc_id": doc_id, "customer_id": self.cust(cid), "type": dtype, "title": title, "dated": dated,
                "file": rel}
        self.f["evidence"].append(meta)
        return meta

    def doc_id(self, doc_id):
        return [e for e in self.f["evidence"] if e["doc_id"] == doc_id][0]

    def list_customer(self, cid, n=1):
        cu = self.customer(cid)
        self.f["internal_list"].append({
            "entry_id": "%s-LTRAP%02d" % (cid[:2], n), "kind": "individual", "name": cu["name"],
            "date_of_birth": cu.get("date_of_birth"), "identifiers": {}, "address": cu.get("address"),
            "reason": "Exited customer: relationship ended in 2025.", "added_on": "2025-06-01"})

    def save(self):
        self.f["cases"] = [c for c in self.f["cases"] if c["case_id"] in self.keep]
        for n, items in self.f.items():
            write(os.path.join(self.dir, n + ".jsonl"), items)


RULE_NAMES = {"TM01": "Activity exceeds the expected profile", "TM03": "Repeated transactions below a threshold",
              "TM08": "Third-party funding or payout", "TM22": "Counterparty on an internal list"}
NOT_CLOSE = {"RFI", "ESCALATE"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def long_date(iso):
    y, m, d = iso[:10].split("-")
    return "%d %s %s" % (int(d), MONTHS[int(m) - 1], y)


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
        for inst, rounds in PLANT.items():
            src = os.path.join(base, inst)
            before = run_copy_baseline(src, tmp)
            for n, plant in enumerate(rounds if isinstance(rounds, list) else [rounds]):
                C = Copy(src, os.path.join(tmp, "edit_%s_%d" % (inst, n)))
                tests = plant(C, before)
                C.save()
                after = run(C.dir, os.path.join(tmp, "edit_%s_%d" % (inst, n)))
                for cid, label, want in tests:
                    got, code = after[cid]["verdict"], after[cid]["reason_code"]
                    if isinstance(want, tuple):
                        ok = (got, code) == want
                    else:
                        ok = got in want
                    results.append((ok, inst, label, cid, before[cid]["verdict"], got, code))
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


# ---------------------------------------------------------------------------
# Regression scenarios from the review-fixes hand-over. Each round runs on a
# fresh copy, so several variants of the same case can be tested.

DARIUS_TAGS = {
    "bank": ("event:solicitor_letter", "takings", "hospital", "loan_car", "collection", "refund"),
    "emoney": ("event:settlement_letter", "tenancy", "own_account"),
    "investment": ("gift", "redundancy", "own_account"),
    "payments_fx": ("supply_contract", "contract_schedule", "group", "returning", "own_account", "payment_file"),
}


def darius(inst, mode):
    """An extra alerted £4,321.17 in from a stranger, as a new TM01 alert or added to an existing alert."""
    def plant(C, tags):
        t = []
        for i, tag in enumerate(DARIUS_TAGS[inst]):
            cid = pick(tags, tag)[0]
            frm = C.case[cid]["review_window"]["from"]
            x = C.new_tx(cid, 900 + i, direction="in", channel="faster_payment", amount_gbp=4321.17,
                         booked_at=shift(frm + "T10:00:00Z", 5), counterparty_name="Darius Kellow",
                         counterparty_account="12-34-56 87654321", counterparty_country="United Kingdom",
                         reference="PAYMENT", location=None)
            if mode == "alert":
                C.add_alert(cid, "TM01", [x["transaction_id"]], n=90 + i,
                            measured={"received_gbp": 4321.17, "baseline_gbp": 1400, "multiple": 3.09,
                                      "limit_multiple": 3, "window_days": 30})
            else:
                C.append_to_alert(cid, x["transaction_id"])
            t.append((cid, "stranger's £4,321.17 (%s) %s" % (mode, tag), NOT_CLOSE))
        C.keep |= {x[0] for x in t}
        return t
    return plant


def rename_rows(C, doc_id, prefix):
    meta = C.doc_id(doc_id)
    p = os.path.join(C.dir, meta["file"])
    lines = open(p, encoding="utf-8").read().splitlines()
    out, n, in_table = [], 0, False
    for l in lines:
        if l.startswith("|---"):
            in_table = True
        elif in_table and l.startswith("| "):
            cells = l.split("|")
            n += 1
            cells[1] = " %s %d " % (prefix, n)
            l = "|".join(cells)
        out.append(l)
    open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")


def px_round_a(C, tags):
    t = []
    x = C.tx["PX-T483011855"]
    x.update(counterparty_name="Velmora Capital FZE", counterparty_country="United Arab Emirates")
    C.add_alert("PX-CS102442", "TM08", ["PX-T483011855"], n=1,
                measured={"flows": 1, "total_gbp": x["amount_gbp"], "limit_gbp": 1000})
    t.append(("PX-CS102442", "own-account funding from Velmora Capital FZE", NOT_CLOSE))
    rename_rows(C, "PAY-4716", "Unlisted Contractor")
    t.append(("PX-CS198509", "payment file payees all renamed", NOT_CLOSE))
    for tid in ("PX-T180299143", "PX-T434639066", "PX-T280382031", "PX-T112969814"):
        C.tx[tid].update(counterparty_name="Varnell Holdings Ltd", counterparty_country="Cyprus")
    t.append(("PX-CS121073", "stage payments to Varnell Holdings Ltd", ("ESCALATE", "ESC_INVOICE_MISMATCH")))
    C.tx["PX-T717987710"]["counterparty_name"] = "Zarif Exchange Co"
    t.append(("PX-CS118701", "supplier payment to Zarif Exchange Co", ("ESCALATE", "ESC_INVOICE_MISMATCH")))
    for tid, name in (("PX-T505656923", "Marlow Exports Ltd"), ("PX-T413027221", "Penrose Trading Ltd"),
                      ("PX-T982913829", "Quayside Brands Ltd")):
        C.tx[tid]["counterparty_name"] = name
    t.append(("PX-CS159922", "freight payers not on INV-8889", NOT_CLOSE))
    held = C.new_tx("PX-CS130281", 1, direction="out", channel="swift", amount_gbp=12345.67,
                    booked_at="2026-10-14T16:00:00Z", counterparty_name="Corvane Supplies Ltd",
                    counterparty_account="IBAN ending 4471", counterparty_country="Germany", reference="INV 5521")
    C.case["PX-CS130281"]["held_payment_id"] = held["transaction_id"]
    C.add_doc("PX-CS130281", "NTE-99001", "contact_note", "Customer contact note", "2026-10-14",
              "The client called to ask when the £12,345.67 payment to Corvane Supplies Ltd would be released.")
    t.append(("PX-CS130281", "new held £12,345.67 payment, only a contact note", NOT_CLOSE))
    x = C.new_tx("PX-CS196750", 2, direction="out", channel="swift", amount_gbp=7654.32,
                 booked_at="2026-09-12T11:00:00Z", counterparty_name="Brisa Components Co",
                 counterparty_account="IBAN ending 8812", counterparty_country="Spain", reference="ORDER 1187")
    C.append_to_alert("PX-CS196750", x["transaction_id"])
    t.append(("PX-CS196750", "extra alerted £7,654.32 to a new payee", NOT_CLOSE))
    C.keep |= {x[0] for x in t}
    return t


def px_round_b(C, tags):
    t = []
    meta = C.doc_id("PAY-4716")
    p = os.path.join(C.dir, meta["file"])
    keep = [l for l in open(p, encoding="utf-8").read().splitlines() if not l.startswith("|")]
    open(p, "w", encoding="utf-8").write("\n".join(keep) + "\n")
    t.append(("PX-CS198509", "payment file table removed", NOT_CLOSE))
    x = C.tx["PX-T180299143"]
    x["booked_at"] = shift(x["booked_at"], -40)
    t.append(("PX-CS121073", "stage 1 paid 40 days early", NOT_CLOSE))
    C.tx["PX-T999864836"]["counterparty_name"] = "Tellwick Trading Co"
    t.append(("PX-CS196750", "first payment after the pause to another payee", NOT_CLOSE))
    C.keep |= {x[0] for x in t}
    return t


def px_round_c(C, tags):
    cu = C.customer("PX-CS198509")
    C.add_doc("PX-CS198509", "INV-99002", "supplier_invoice", "Supplier invoice", "2026-09-10",
              "**Supplier:** Kestrel Code Studio, Poland\n**Buyer:** %s\n**Goods:** Software development services\n"
              "**Amount:** £3,250.00\n**Pay to:** IBAN ending 6610" % cu["name"])
    C.keep.add("PX-CS198509")
    return [("PX-CS198509", "+ supplier invoice for software development", ("CLOSE", "INVOICE_MATCHES"))]


def riley_gift(C, cid, first):
    """A second TM22 alert on IV-L25058 (Riley Selholt), with a gift letter giving the listed birth date."""
    cu = C.customer(cid)
    x = C.new_tx(cid, 10 if first else 11, direction="in", channel="faster_payment", amount_gbp=12000.0,
                 booked_at=shift(C.case[cid]["review_window"]["from"] + "T10:00:00Z", 6),
                 counterparty_name="Riley Selholt", counterparty_account="50-61-60 51102217",
                 counterparty_country="United Kingdom", reference="GIFT", location=None)
    C.add_doc(cid, "GFT-99003", "gift_letter", "Gift letter from Riley Selholt", x["booked_at"][:10],
              "I, Riley Selholt, born 17 February 1959, am providing a gift of £12,000.00 to my relative, %s. "
              "This is a gift and does not need to be repaid." % cu["name"])
    C.add_alert(cid, "TM22", [x["transaction_id"]], first=first, n=22,
                measured={"list_entry": "IV-L25058", "matched_on": "name", "matched_party": "Riley Selholt"})


def iv_round_a(C, tags):
    t = []
    C.tx["IV-T802063304"]["counterparty_name"] = "Marek Dolan"
    C.add_alert("IV-CS112234", "TM08", ["IV-T802063304"], n=1,
                measured={"flows": 1, "total_gbp": 27759, "limit_gbp": 1000})
    t.append(("IV-CS112234", "ISA refund paid to Marek Dolan", ("ESCALATE", "ESC_THIRD_PARTY_FUNDING")))
    C.tx["IV-T892985591"]["counterparty_name"] = "Marek Dolan"
    C.add_alert("IV-CS134100", "TM08", ["IV-T892985591"], n=2,
                measured={"flows": 1, "total_gbp": 16273.53, "limit_gbp": 1000})
    t.append(("IV-CS134100", "fund bought with Marek Dolan's money (TM08)", {"ESCALATE"}))
    C.edit_doc(C.doc_id("GFT-2351"), "to my child, Riley Holldale", "to my nephew, Sam Okafor")
    t.append(("IV-CS103503", "gift letter names someone else", NOT_CLOSE))
    riley_gift(C, "IV-CS169053", first=False)
    t.append(("IV-CS169053", "second list match, same birth date (alert last)", ("ESCALATE", "ESC_LISTED_PARTY")))
    dob = C.customer("IV-CS106722")["date_of_birth"]
    C.add_doc("IV-CS106722", "EML-00001", "customer_email", "Email from the customer", "2026-08-10",
              "I am expecting a transfer from Riley Selholt next week.\n\n**Date of birth:** %s" % long_date(dob))
    t.append(("IV-CS106722", "+ email naming Riley Selholt, customer's DOB on another line", {"ESCALATE"}))
    C.keep |= {x[0] for x in t}
    return t


def iv_round_b(C, tags):
    t = []
    C.tx["IV-T802063304"]["counterparty_account"] = "40-00-00 99887766"
    t.append(("IV-CS112234", "ISA refund to another account", NOT_CLOSE))
    C.tx["IV-T892985591"]["counterparty_name"] = "Marek Dolan"
    t.append(("IV-CS134100", "fund bought with Marek Dolan's money (no TM08)", {"ESCALATE"}))
    riley_gift(C, "IV-CS169053", first=True)
    t.append(("IV-CS169053", "second list match, same birth date (alert first)", ("ESCALATE", "ESC_LISTED_PARTY")))
    C.add_doc("IV-CS106722", "NTE-99004", "contact_note", "Customer contact note", "2026-08-11",
              "The customer mentioned a friend, Riley Selholt, born 3 March 1961.")
    t.append(("IV-CS106722", "+ note giving a different birth date", {"ESCALATE"}))
    C.keep |= {x[0] for x in t}
    return t


def iv_round_c(C, tags):
    C.tx["IV-T707667778"]["counterparty_name"] = "Marek Dolan"
    C.keep.add("IV-CS134100")
    return [("IV-CS134100", "early-sale proceeds paid to Marek Dolan", NOT_CLOSE)]


def bk_round_a(C, tags):
    t = []
    base = C.tx["BK-T605711576"]
    for i in range(3):
        x = C.add_tx("BK-CS107843", base, 20 + i, booked_at=shift(base["booked_at"], i + 1))
        C.alert["BK-AL5401834"]["transaction_ids"].append(x["transaction_id"])
    t.append(("BK-CS107843", "3 extra deposits copying a takings amount", NOT_CLOSE))
    C.tx["BK-T803954552"]["counterparty_account"] = "99-99-99 99887766"
    t.append(("BK-CS114405", "savings from an account not on the statement", NOT_CLOSE))
    cid = pick(tags, "loan_car")[0]
    pay = [x for x in C.alerted(cid) if x["direction"] == "out"][0]
    pay["counterparty_name"] = "Quentin Marsh"
    t.append((cid, "car payment sent to another name", NOT_CLOSE))
    cid = "BK-CS125943"  # an ESCALATE ESC_LISTED_PARTY officer match on the dev data
    for d in C.f["evidence"]:
        if d["customer_id"] == C.cust(cid) and d["type"] in ("company_register_extract", "significant_control_register"):
            p = os.path.join(C.dir, d["file"])
            txt = open(p, encoding="utf-8").read()
            person = C.alert[C.case[cid]["alert_ids"][0]]["measured"]["matched_party"].split(",")[0]
            txt = "\n".join(re.sub(r"(\w+) (19\d\d)", lambda m: "%s %d" % (m.group(1), int(m.group(2)) + 7), l)
                            if l.startswith("| " + person) else l for l in txt.splitlines())
            open(p, "w", encoding="utf-8").write(txt + "\n")
    C.alert[C.case[cid]["alert_ids"][0]]["measured"]["matched_on"] = "company officer name"
    t.append((cid, "officer match, register birth year differs", ("CLOSE", "LIST_MATCH_DIFFERENT_PARTY")))
    C.list_customer("BK-CS103088")
    t.append(("BK-CS103088", "customer on the internal list", {"ESCALATE"}))
    cu = C.customer("BK-CS109535")
    C.add_doc("BK-CS109535", "STM-99005", "bank_statement", "Bank statement", "2026-08-14",
              "**Account holder:** %s\n**Bank:** Northgate Bank\n**Account:** 11-22-33 44556677\n\n"
              "1 August 2026: £200.00 received into the savings account." % cu["name"])
    t.append(("BK-CS109535", "family corridor + unrelated savings statement", ("ESCALATE", "ESC_HIGH_RISK_CORRIDOR")))
    C.add_doc("BK-CS106928", "NTE-99006", "staff_note", "Note by branch staff", "2026-09-10",
              "The customer called to say they were unable to come into the branch this week and asked for "
              "statements by post.")
    t.append(("BK-CS106928", "+ staff note 'unable to come into the branch'", ("CLOSE", "DOCUMENTED_EVENT")))
    C.edit_doc(C.doc_id("NTE-9706"), "for future transactions.",
               "for future transactions. When asked, the customer declined to say where the cash came from.")
    t.append(("BK-CS408982", "NTE-9706 + declined to say where the cash came from", ("ESCALATE", "ESC_STRUCTURING")))
    C.keep |= {x[0] for x in t}
    return t


def bk_round_b(C, tags):
    t = []
    C.add_doc("BK-CS107843", "NTE-99007", "staff_note", "Note by branch staff", "2026-09-14",
              "The customer paid in the festival takings. They did not ask to avoid any checks. I explained the "
              "£10,000 reporting threshold as part of our usual process.")
    t.append(("BK-CS107843", "+ note: did not ask to avoid checks", ("CLOSE", "BUSINESS_RECORDS_RECONCILE")))
    C.add_alert("BK-CS114405", "TM01", ["BK-T803954552"], n=3,
                measured={"received_gbp": 14700, "baseline_gbp": 2100, "multiple": 7.0, "limit_multiple": 3,
                          "window_days": 30})
    t.append(("BK-CS114405", "extra TM01 alert at 7.0 times", ("RFI", "RFI_UPDATED_PROFILE")))
    C.add_doc("BK-CS106928", "NTE-99008", "staff_note", "Note by branch staff", "2026-09-10",
              "The customer asked about the cash reporting threshold for a future house deposit.")
    t.append(("BK-CS106928", "+ note on the threshold for a house deposit", ("CLOSE", "DOCUMENTED_EVENT")))
    C.keep |= {x[0] for x in t}
    return t


def bk_round_c(C, tags):
    me = C.cust("BK-CS106928")
    C.add_doc("BK-CS106928", "DEV-99009", "device_export", "Device and login export", "2026-09-19",
              "| Device | First seen | Customers who logged in from it | Login countries |\n"
              "|---|---|---|---|\n| A51-7781 | 2 March 2026 | %s, BK-C100109 | United Kingdom |" % me)
    C.keep.add("BK-CS106928")
    return [("BK-CS106928", "+ device shared with one other customer", ("CLOSE", "DOCUMENTED_EVENT"))]


def bk_round_d(C, tags):
    C.list_customer("BK-CS106928")
    meta = C.doc_id("SOL-9526")
    meta["dated"] = "2026-11-02"
    C.edit_doc(meta, "**Dated:** 14 September 2026", "**Dated:** 2 November 2026")
    C.keep.add("BK-CS106928")
    return [("BK-CS106928", "customer listed, solicitor letter dated 2 Nov 2026", {"ESCALATE"})]


def em_round_a(C, tags):
    t = []
    cid = "EM-CS110296"
    ids = []
    for i, (amt, shop) in enumerate(((940, "Radcote Convenience Store"), (955, "Post office, Hartley Street"),
                                     (970, "Calwell Convenience Store"), (985, "Selby News and Food"))):
        x = C.new_tx(cid, 30 + i, direction="in", channel="cash_topup", amount_gbp=float(amt),
                     booked_at="2026-09-0%dT12:00:00Z" % (3 + i), counterparty_name=None, counterparty_account=None,
                     counterparty_country="United Kingdom", reference="CASH TOP UP", location=shop)
        ids.append(x["transaction_id"])
    C.add_alert(cid, "TM03", ids, n=4, measured={"count": 4, "total_gbp": 3850, "locations": 4, "limit_count": 3,
                                                 "window_days": 10})
    t.append((cid, "+ TM03 alert on 4 cash top-ups of £940-£985", NOT_CLOSE))
    cid = pick(tags, "event:settlement_letter")[0]
    C.add_doc(cid, "CHT-99010", "support_chat", "Chat with the customer", "2026-08-20",
              "**Customer:** I want to move savings to my crypto account. Is there a limit?\n"
              "**Agent:** There is no limit for transfers to your own accounts.")
    t.append((cid, "+ chat about moving savings to own crypto account", ("CLOSE", "DOCUMENTED_EVENT")))
    C.keep |= {x[0] for x in t}
    return t


PLANT = {
    "bank": [plant_bank, bk_round_a, bk_round_b, bk_round_c, bk_round_d, darius("bank", "alert"),
             darius("bank", "append")],
    "payments_fx": [plant_px, px_round_a, px_round_b, px_round_c, darius("payments_fx", "alert"),
                    darius("payments_fx", "append")],
    "investment": [plant_iv, iv_round_a, iv_round_b, iv_round_c, darius("investment", "alert"),
                   darius("investment", "append")],
    "emoney": [em_round_a, darius("emoney", "alert"), darius("emoney", "append")],
}

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "institutions"))
