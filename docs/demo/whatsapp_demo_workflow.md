# AskHelm WhatsApp Demo Workflow

## Purpose

Controlled demo and regression test script for AskHelm.

Use dummy, redacted or generic demo data only.

Do not use live vessel, owner, crew, supplier or confidential operational data.

## 1. Commercial approval

### Demo 01 — Clean match

Expected decision: APPROVE

Commands:
- upload Demo 01 quote
- upload Demo 01 invoice
- what changed?
- ok to pay?

Expected:
- quote and invoice match
- no material discrepancy
- decision APPROVE
- risk LOW

### Demo 02 — Added shipping / freight

Expected decision: QUERY

Commands:
- upload Demo 02 quote
- upload Demo 02 proforma / invoice
- what changed?
- ok to pay?

Expected:
- parts match
- added packing/shipping detected
- VAT on added charge detected
- decision QUERY
- risk MEDIUM

### Demo 03 — Substitution / address mismatch

Expected decision: HOLD

Commands:
- upload Demo 03 quote
- upload Demo 03 invoice
- what changed?
- can I approve this?

Expected:
- substitution detected
- part/specification change detected
- price increase detected
- delivery/address mismatch detected
- decision HOLD
- risk HIGH

## 2. Inventory

Commands:
- show stock
- show valve stock
- show pump stock
- show filter stock
- show HEM spares
- how many AIK111571 onboard?
- do we need to order more AIK111571?

Expected:
- stock results returned
- exact part-number queries prioritised
- quantity and location shown
- no blank quantity output
- weak equipment links suppressed

## 3. Quote-to-stock follow-up

Commands:
- upload quote containing known stock item
- do we already have these onboard?
- check these against stock
- do we need to order these?

Expected:
- extracted quote line items checked against stock
- stock matches shown
- not-found items shown
- quantity and location shown
- practical order guidance given

## 4. Deck inventory

Commands:
- show deck stock
- show watersports inventory
- where are the ratchet straps?
- where is the teak oil?
- show low deck stock

Expected:
- deck inventory searched separately from engineering stock
- location and quantity shown where available

## 5. Role-based responses

Commands:
- set my role to captain
- ok to pay?
- set my role to purser
- ok to pay?
- set my role to engineer
- do we need to order more AIK111571?
- set my role to deck officer
- show deck stock

Expected:
- decision remains consistent
- emphasis changes by role
- Captain: risk and operational decision
- Purser: cost, VAT and payment confidence
- Engineer: equipment, spares, stock and maintenance impact
- Deck Officer: readiness, deck stock, safety and operations

## 6. Compliance

Commands:
- show regulations
- show compliance profile
- what does ISM say about non-conformity?
- my fire pump test is overdue, is this ok?
- does Tier III apply in the Norwegian Sea?

Expected:
- answer only from loaded compliance sources
- no bluffing outside loaded documents
- decision / why / source / actions format used

## Pass criteria

A demo pass requires:

- Demo 01 returns APPROVE
- Demo 02 returns QUERY
- Demo 03 returns HOLD
- direct stock queries work
- generic stock searches work
- deck stock queries work
- quote-to-stock follow-up works
- role-based emphasis works
- compliance answers remain grounded
- no DOCUMENT NOT UNDERSTOOD for supported commands
