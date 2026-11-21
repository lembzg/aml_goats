"""Entry point: python3 main.py <institution folder> <output file>"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data import Institution, fmt_date, to_date  # noqa: E402
from context import Ctx, Decision  # noqa: E402
from rules import ESCALATION_RULES, rule_listed_party, listed_decision, D, _dob_text  # noqa: E402
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

GENERIC_RFI = ("The activity on this case could not be matched to the documents on file. Ask the "
               "customer to explain and evidence the alerted transactions.")


def _debug_error():
    if os.environ.get("TM_DEBUG"):
        traceback.print_exc()


def _safe_rfi(c, listed=None):
    try:
        return rule_rfi(c, listed) if listed is not None else rule_rfi(c)
    except Exception:
        _debug_error()
        return Decision("RFI", "RFI_SOURCE_OF_FUNDS", GENERIC_RFI, [], "error")


def decide(c):
    """The case's verdict before the final checks. Signs of crime first, then the internal list, then the
    rules that close, and otherwise a request for information.

    A rule that fails on unexpected data never lets a case close: a failed escalation rule blocks any close
    (its check did not happen), a failed list check sends an RFI, and a failed close rule is skipped."""
    esc_error = False
    for rule in ESCALATION_RULES:
        try:
            d = rule(c)
        except Exception:
            _debug_error()
            esc_error = True
            continue
        if d:
            return d, esc_error

    listed = None
    if "TM22" in c.rules:
        try:
            res = rule_listed_party(c)
            esc = listed_decision(c, res) if res is not None and not isinstance(res, Decision) else None
        except Exception:
            _debug_error()
            return _safe_rfi(c), esc_error
        if isinstance(res, Decision):
            if res.verdict != "CLOSE" or (c.rules == {"TM22"} and not res.needs_closer):
                return res, esc_error
            listed = ("cleared", res)
        elif res is not None:
            if esc:
                return esc, esc_error
            listed = res

    for rule in CLOSE_RULES:
        try:
            d = rule(c)
        except Exception:
            _debug_error()
            continue
        if d:
            if listed is None:
                return d, esc_error
            if d.verdict != "CLOSE":
                return d, esc_error
            kind = listed[0]
            if kind == "cleared":
                ld = listed[1]
                return D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", ld.reason + " " + d.reason,
                         ld.evidence + d.evidence, ld.tag + "+" + d.tag), esc_error
            if kind == "different":
                _, doc, dob, entry, t = listed
                lead = ("The list match is a different person: %s gives %s's date of birth as %s, but list "
                        "entry %s was born on %s." % (doc.id, entry["name"], _dob_text(dob), entry["entry_id"],
                                                      fmt_date(to_date(entry.get("date_of_birth")))))
                return D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", lead + " " + d.reason,
                         [doc.id, entry["entry_id"]] + d.evidence, "list_diff+" + d.tag), esc_error
            break  # list unresolved: fall through to RFI

    if listed is not None and listed[0] in ("unknown",):
        return _safe_rfi(c, listed), esc_error
    return _safe_rfi(c), esc_error


def sanitize(c, d):
    if d.code not in VERDICT_CODES.get(d.verdict, set()):
        d.code = sorted(VERDICT_CODES[d.verdict])[0]
    known = {t["transaction_id"] for t in c.txs} | {x.id for x in c.docs} | set(c.inst.internal_list) | {
        r["review_id"] for r in c.reviews if r.get("review_id")}
    seen, ev = set(), []
    for e in d.evidence:
        if e in known and e not in seen:
            seen.add(e)
            ev.append(e)
    d.evidence = ev
    return d


def decide_case(inst, case):
    """The final decision for one case, never raising."""
    try:
        c = Ctx(inst, case)
    except Exception:
        _debug_error()
        return Decision("RFI", "RFI_SOURCE_OF_FUNDS", GENERIC_RFI, [], "error")
    try:
        d, esc_error = decide(c)
        if d.verdict == "CLOSE" and esc_error:
            d = _safe_rfi(c)  # a check for signs of crime did not run, so the case must not close
        try:
            d = check_close(c, d)
        except Exception:
            _debug_error()
            if d.verdict == "CLOSE":
                d = _safe_rfi(c)  # the final checks did not run, so the case must not close
        # at the payments firm a case is the held payment: always cite it
        if c.held and c.held["transaction_id"] not in d.evidence:
            d.evidence = list(d.evidence) + [c.held["transaction_id"]]
        return sanitize(c, d)
    except Exception:
        _debug_error()
        return Decision("RFI", "RFI_SOURCE_OF_FUNDS", GENERIC_RFI, [], "error")


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: run <institution folder> <output file>\n")
        return 2
    folder, out = argv[1], argv[2]
    if not os.path.isdir(folder):
        sys.stderr.write("error: institution folder not found: %s\n" % folder)
        return 2
    if not os.path.isfile(os.path.join(folder, "cases.jsonl")):
        sys.stderr.write("error: no cases.jsonl in %s\n" % folder)
        return 2
    inst = Institution(folder)
    debug = os.environ.get("TM_DEBUG")
    lines = []
    for case in inst.cases:
        d = decide_case(inst, case)
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
