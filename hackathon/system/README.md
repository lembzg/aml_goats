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
   - **Earlier reviews:** they are cited as context and never treated as covering new or larger activity.
   - **Payments abroad on someone else's behalf** (a relative's hospital bill, and the same checks for school fees or a funeral): the money must go to the payee the bill names (otherwise ESC_INVOICE_MISMATCH); it must be the only payment to that payee or that higher-risk country (otherwise ESC_HIGH_RISK_CORRIDOR); no third party may have paid the customer a similar sum in the week before (otherwise ESC_THIRD_PARTY_FUNDING); and the beneficiary must share the customer's surname (otherwise RFI_PURPOSE_DOCUMENTS, asking for proof of the relationship). A beneficiary with the customer's own full name is allowed, since a parent can share both names.
4. **Otherwise RFI.** The engine asks for the one document that would settle the case, such as the solicitor's letter for an estate payment, the booking for a group trip, the loan agreement and invoice for a car, or a statement for an own-name account. The reason says exactly what to ask for.

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
