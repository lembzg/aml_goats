"""Entry point: python3 main.py <institution folder> <output file>"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data import Institution  # noqa: E402
from context import Ctx, Decision  # noqa: E402
from rules import ESCALATION_RULES, rule_listed_party, listed_decision, D  # noqa: E402
from closers import CLOSE_RULES  # noqa: E402
from rfi import rule_rfi  # noqa: E402
from guards import check_close  # noqa: E402

VERDICT_CODES = {
    "CLOSE": {"COLLECTION_DOCUMENTED", "BUSINESS_RECORDS_RECONCILE", "DOCUMENTED_EVENT", "INVOICE_MATCHES",
              "OWN_ACCOUNT", "GROUP_COMPANY", "THIRD_PARTY_DOCUMENTED", "REFUND_OF_EARLIER_PAYMENT",
              "RETURNING_CUSTOMER", "LIST_MATCH_DIFFERENT_PARTY", "WITHIN_PROFILE"},
    "RFI": {"RFI_PURPOSE_DOCUMENTS", "RFI_BUSINESS_RECORDS", "RFI_SOURCE_OF_FUNDS",
            "RFI_COUNTERPARTY_DETAILS", "RFI_UPDATED_PROFILE"},
    "ESCALATE": {"ESC_MULE_ACTIVITY", "ESC_STRUCTURING", "ESC_UNEXPLAINED_FUNDS", "ESC_PASS_THROUGH",
                 "ESC_INVOICE_MISMATCH", "ESC_HIGH_RISK_CORRIDOR", "ESC_ACCOUNT_MISUSE",
                 "ESC_THIRD_PARTY_FUNDING", "ESC_EARLY_REDEMPTION", "ESC_LISTED_PARTY"},
}


def decide(c):
    for rule in ESCALATION_RULES:
        d = rule(c)
        if d:
            return d

    listed = None
    if "TM22" in c.rules:
        res = rule_listed_party(c)
        if isinstance(res, Decision):
            if res.verdict != "CLOSE" or c.rules == {"TM22"}:
                return res
            listed = ("cleared", res)
        elif res is not None:
            esc = listed_decision(c, res)
            if esc:
                return esc
            listed = res

    for rule in CLOSE_RULES:
        d = rule(c)
        if d:
            if listed is None:
                return d
            if d.verdict != "CLOSE":
                return d
            kind = listed[0]
            if kind == "cleared":
                ld = listed[1]
                return D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", ld.reason + " " + d.reason,
                         ld.evidence + d.evidence, ld.tag + "+" + d.tag)
            if kind == "different":
                _, doc, dob, entry, t = listed
                lead = ("The list match is a different person: %s gives %s's date of birth as %s, but list "
                        "entry %s was born on %s." % (doc.id, entry["name"], _fd(dob), entry["entry_id"],
                                                      _fd(entry.get("date_of_birth"))))
                return D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", lead + " " + d.reason,
                         [doc.id, entry["entry_id"]] + d.evidence, "list_diff+" + d.tag)
            break  # list unresolved: fall through to RFI

    if listed is not None and listed[0] in ("unknown",):
        return rule_rfi(c, listed)
    if listed is not None and listed[0] == "different":
        return rule_rfi(c)
    return rule_rfi(c)


def _fd(d):
    from data import fmt_date, to_date
    return fmt_date(to_date(d))


def sanitize(c, d):
    if d.code not in VERDICT_CODES.get(d.verdict, set()):
        d.code = sorted(VERDICT_CODES[d.verdict])[0]
    known = set(c.inst.tx) | {x.id for x in c.docs} | set(c.inst.internal_list) | {
        r["review_id"] for r in c.reviews}
    d.evidence = [e for e in d.evidence if e in known]
    return d


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: run <institution folder> <output file>\n")
        return 2
    folder, out = argv[1], argv[2]
    inst = Institution(folder)
    debug = os.environ.get("TM_DEBUG")
    lines = []
    for case in inst.cases:
        try:
            c = Ctx(inst, case)
            d = decide(c)
            d = check_close(c, d) or d
            d = sanitize(c, d)
        except Exception:  # never drop a case
            if debug:
                traceback.print_exc()
            d = Decision("RFI", "RFI_SOURCE_OF_FUNDS",
                         "The activity on this case could not be matched to the documents on file. Ask the "
                         "customer to explain and evidence the alerted transactions.", [], "error")
        row = {"case_id": case["case_id"], "alert_ids": list(case.get("alert_ids", [])),
               "verdict": d.verdict, "reason_code": d.code, "reason": d.reason, "evidence": d.evidence}
        if debug:
            row["_tag"] = d.tag
        lines.append(json.dumps(row, ensure_ascii=False))
    d = os.path.dirname(os.path.abspath(out))
    os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
