# Alert review workbench

A local screen for reviewing the system's decisions the way an analyst works a case.

## Start it

From the `hackathon` folder:

```
python3 ui/serve.py
```

The page opens at http://127.0.0.1:8000. Standard Python only, nothing to install.

To review a different set of cases, such as a new test folder, pass the folders:

```
python3 ui/serve.py <institutions folder> <output folder> --port 8000
```

The institutions folder holds one folder per institution (`bank`, `emoney`, ...). The output folder holds the system's `<institution>.jsonl` files.

## What it shows

The header contains the AML label, Case review title and Industries dropdown spaced across the page. The dropdown switches between the four institution datasets. The workbench uses a monochrome interface with one Arial/Helvetica font family, three text sizes (12, 14 and 28px), two weights (regular and bold), the customer first, followed by three sections: Decision (analyst decision and system recommendation), Evidence, and Case details (with the alert explanation open). Case text uses one colour and regular weight, with bold reserved for the three section headings. Customer profile, transactions, documents, list entries and earlier reviews can be expanded as needed. Evidence links show the document title, payment details or record type alongside its ID; selecting View opens the record automatically. Verdicts retain explicit labels and distinct marker shapes. No external font downloads are required.

Previous and Next move through the current filtered queue. Changing a filter keeps the current case if it still matches, otherwise selects the first match. If there are no matches, the case pane shows an empty state. Older loading responses cannot replace a newer selection. Em dashes in displayed source text are presented as semicolons without changing the underlying records.

- The case queue for each institution, filterable by verdict and alert rule, searchable by case ID, customer or reason code.
- For each case:
  - the decision, reason code and reason;
  - the evidence IDs it relies on (select one to jump to that transaction, document, list entry or review);
  - the alerts and the customer's declared profile;
  - transactions, with alerted, held and relied-on items marked;
  - every document on file, with the ones relied on marked;
  - earlier reviews and internal list entries.

Use the up and down arrow keys in the queue to move between cases. Each case has its own link, for example `#bank/BK-CS365131`.

## Analyst decisions

Use Case decision to select Close, Request information or Escalate, add a reason, and select Save decision. You can edit the note or use the system recommendation again. The queue and filters reflect the saved analyst decision, while the original system recommendation and evidence stay visible.

Analyst decisions are stored in this browser's localStorage, separately for each industry and case. They survive refreshes in the same browser and origin. They are not shared with other browsers or analysts; clearing browser data removes them. A failed storage write shows an error and does not update the displayed saved decision. This is a local review workspace, not a payment-release or reporting workflow.

The workbench never changes institution data or the system's output, and it lives outside `system/`, so it has no effect on the graded run.
