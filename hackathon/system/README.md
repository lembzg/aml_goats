# How the system decides

`system/run <institution folder> <output file>` reads one institution folder and writes one JSON line per case. It is a deterministic rules engine written in Python 3, using the standard library only. It calls no language model, needs no network, and gives the same output on every run from any working directory. It writes nothing except the output file.

```
system/
  run                 shell entry point
  engine/main.py      loads the folder, runs the rules in order, writes the output
  engine/data.py      reads the JSONL files and parses the Markdown documents (fields, tables, amounts, dates)
  engine/context.py   everything about one case: customer, alerts, alerted transactions, documents, reviews
  engine/rules.py     escalation rules and the internal-list (TM22) logic
  engine/closers.py   rules that close a case when documents explain it
  engine/rfi.py       requests for information when the activity is plausible but undocumented
  engine/guards.py    final checks that can block any close
  tests/test_traps.py plants each trap into a copy of a real closed case
```

## Order of decisions

Each case is one customer, or at the payments firm one held payment. The case gets one verdict covering every alert, and the rules run in this order.

1. **Signs of crime first, so that no document can hide them.** Any of these escalates the case:
   - **ESC_MULE_ACTIVITY:** a sending bank has reported a payer as a scam victim, or the customer says they forward money to a crypto account and keep a cut.
   - **ESC_ACCOUNT_MISUSE:** a device export shows the device logging in to several customers' accounts.
   - **ESC_STRUCTURING:** a staff note records the customer asking whether keeping amounts under a threshold avoids checks.
   - **ESC_UNEXPLAINED_FUNDS:** the register shows a paying company was dissolved before it paid; or the customer gives contradictory sources for cash that was sent straight on.
   - **ESC_PASS_THROUGH:** money paid out within a day to companies the register shows the customer's own director controls.
   - **ESC_HIGH_RISK_CORRIDOR:** money from unrelated third parties split across several recipients in an undeclared higher-risk country, explained only as "family".
   - **ESC_THIRD_PARTY_FUNDING:** the customer says other people invest through their account; or a payment file pays many "independent" payees who share a handful of addresses, funded by an unrelated third party.
   - **ESC_EARLY_REDEMPTION:** fund units bought with third-party money and sold back quickly to pay other people.
   - **ESC_INVOICE_MISMATCH:** the invoices are for goods or services the business does not deal in, such as phone handsets for a caterer or consultancy for a produce importer, or a staff note records the director being evasive.

   Customer chats are used for these checks, but the engine never depends on their exact wording. A second set of checks looks only at the money and the documents:
   - many unrelated people paying in, with the money leaving in repeated payments to one company plus cash withdrawals;
   - several unrelated people funding an investment account with nothing linking them to the customer;
   - early fund sales paid out to someone other than the customer;
   - cash paid in and sent abroad within days, with nothing explaining the cash.

   With every chat and fraud report made unreadable, every development case keeps its verdict.
2. **Internal list (TM22).** The decision follows the policy:
   - **Company officer match.** The engine checks the officer register and the significant-control register at the payment date. If the listed person still controlled the counterparty, even after resigning as director, it escalates with ESC_LISTED_PARTY. If they had left before the payment and an invoice matches, it closes.
   - **Address-only match.** If the register shows different people run the business and the unit differs, it closes with LIST_MATCH_DIFFERENT_PARTY.
   - **Name match.** If a document on file gives the counterparty's date of birth and it equals the list entry's, it escalates. If it differs, it closes, but only when the rest of the case also closes. If nothing settles it, the verdict is RFI_COUNTERPARTY_DETAILS.
3. **Close when the documents explain every alert.** Every close rule checks facts, not just that a document exists:
   - **Amounts:** the amounts in the documents must equal the alerted transactions.
   - **Names:** payers must be named on the booking, gift letter or freight invoices.
   - **Vehicles:** a car's registration must match the insurance schedule.
   - **Refunds:** a refund needs an earlier payment to the same party, at least as large.
   - **Own accounts:** an account counts as the customer's own only when a statement on file shows they hold it. If the TM01 multiple is 5 times the declared income or volume or more, the verdict is RFI_UPDATED_PROFILE, as the policy says.
   - **Earlier reviews:** they are cited as context and never treated as covering new or larger activity. Cash paid back in after a reviewed withdrawal closes only when the bank's own records show a withdrawal of exactly the same amount.
   - **Payments abroad on someone else's behalf** (a relative's hospital bill): the money must go to the payee the bill names (otherwise ESC_INVOICE_MISMATCH); it must be the only payment to that payee or that higher-risk country (otherwise ESC_HIGH_RISK_CORRIDOR); no third party may have paid the customer a similar sum in the week before (otherwise ESC_THIRD_PARTY_FUNDING); and the beneficiary must share the customer's surname (otherwise RFI_PURPOSE_DOCUMENTS, asking for proof of the relationship). A beneficiary with the customer's own full name is allowed, since a parent can share both names.
4. **Final checks on every close** (`engine/guards.py`). A close rule usually proves one fact, such as a document matching one payment. Before any CLOSE is written, the rest of the case is checked, and the close is blocked if:
   - **Money moved on:** money a document explains was sent within three days to a new payee nothing on file explains (ESC_UNEXPLAINED_FUNDS), or within a week to anyone unexplained (RFI, or escalate if it goes to a higher-risk country). Buying fund units counts as the money staying.
   - **Payer not named:** a document explaining money in does not name who paid it (RFI).
   - **Dates don't fit:** the deciding document is dated more than a month after the money moved, more than a year before it, or after the case was opened (RFI_PURPOSE_DOCUMENTS).
   - **A one-off repeats:** similar money came in before a documented one-off event (RFI_SOURCE_OF_FUNDS).
   - **Returned cash with gaps:** no earlier review of the withdrawal, or the cash was paid back in at a different branch (RFI_SOURCE_OF_FUNDS).
   - **Wrong name on a document:** a statement, loan, policy, invoice or receipt relied on is made out to someone other than the customer, or a sale receipt shows the customer as the buyer (RFI).
   - **Customer on the internal list:** the customer matches an entry on name and date of birth (ESC_LISTED_PARTY).
   - **Payments firm invoices:** a held payment of £10,000 or more to someone other than the client has no invoice or contract showing that amount, or an alerted payment of that size goes to a payee the client has never paid before with no invoice (RFI_PURPOSE_DOCUMENTS). Large payments to suppliers the client has paid regularly before are named in the reason instead.

   No check blocks a close on the development cases. Each one is tested by planting its trap into a copy of a real closed case.
5. **Otherwise RFI.** The engine asks for the one document that would settle the case, such as the solicitor's letter for an estate payment, the booking for a group trip, the loan agreement and invoice for a car, or a statement for an own-name account. The reason says exactly what to ask for.

Whatever happens, every case gets a line. If a rule fails unexpectedly the case becomes an RFI, never a silent omission.

## Reasons and evidence

Reasons are 2 to 4 sentences built from the case's own facts: names, amounts, dates, document IDs and list entries. `evidence` lists the documents, transactions, list entries and earlier reviews relied on, decisive ones first. Every ID is checked to exist in the folder before it is written.

## Results on the development cases

| Institution | CLOSE | RFI | ESCALATE |
|---|---|---|---|
| Bank | 500 | 250 | 250 |
| E-money | 500 | 250 | 250 |
| Investment | 500 | 250 | 250 |
| Payments and FX | 500 | 250 | 250 |

A run of 1,000 cases takes about 15 seconds. Nothing from the development cases is built in: no case IDs, names or amounts. The engine reads the higher-risk country list from the institution's `POLICY.md`.

Set `TM_DEBUG=1` to add a `_tag` field naming the rule that decided each case.

## Tests

`python3 system/tests/test_traps.py institutions` plants each edge case above into a copy of a real closed case (in a temporary folder) and checks the system no longer closes it, and that untouched controls still close. It takes about 30 seconds.
