-- Premium passes, paid by UPI straight to the site owner and approved by hand [A305].
--
-- A plain UPI ID tells the site nothing: the payer's app moves the money and the site never
-- hears about it. So a pass is a CLAIM until an admin has seen the payment. The player
-- orders a pass (a row, `pending`, with a reference code to put in the UPI note), pays,
-- and submits the 12-digit UTR their app shows; an admin matches the UTR and reference
-- against their own UPI history and approves or rejects it in the console. Nobody holds a
-- pass the owner has not seen paid for.
--
-- Built so a payment gateway can replace the hand check later without touching passes:
-- whatever confirms a payment only ever moves a row from `pending` to `approved`.

create table premium_passes (
    pass_id        serial primary key,
    account_id     int not null references accounts(account_id) on delete cascade,
    kind           text not null,
    amount_rupees  int not null,
    reference      text not null,
    utr            text,
    status         text not null default 'pending',
    created_at     timestamptz not null default now(),
    claimed_at     timestamptz,
    decided_at     timestamptz,
    decided_by     int,
    starts_on      date,
    ends_on        date,
    note           text,

    constraint premium_passes_kind_ck check (kind in ('season', 'founder')),
    constraint premium_passes_status_ck
        check (status in ('pending', 'approved', 'rejected', 'revoked')),
    constraint premium_passes_amount_ck check (amount_rupees > 0),
    constraint premium_passes_reference_ck check (reference ~ '^[A-Z2-9]{6}$'),
    constraint premium_passes_utr_ck check (utr is null or utr ~ '^[0-9]{12}$'),
    -- A pass that was approved knows when it started; a season pass also when it ends.
    -- A founder pass never ends, so its ends_on stays null.
    constraint premium_passes_approved_dates_ck check (
        status <> 'approved'
        or (starts_on is not null and (kind = 'founder' or ends_on is not null))),
    constraint premium_passes_decided_ck check (status = 'pending' or decided_at is not null)
);

create unique index premium_passes_reference_idx on premium_passes (reference);
-- One payment can back one pass only, however many times its UTR is typed in. A rejected
-- claim stops counting, so a real payer whose UTR somebody else typed first can still use it.
create unique index premium_passes_utr_idx on premium_passes (utr)
    where utr is not null and status <> 'rejected';
create index premium_passes_account_idx on premium_passes (account_id);
create index premium_passes_claims_idx on premium_passes (status, claimed_at)
    where utr is not null;

comment on table premium_passes is
    'One row per pass ordered. pending -> approved | rejected by an admin who has matched the UTR and reference against the payments received; approved -> revoked for a refund. See web/premium.py.';
comment on column premium_passes.reference is
    'Six characters the player puts in the UPI payment note, so the admin can match a payment to its order. Unambiguous alphabet: no 0/O or 1/I.';
comment on column premium_passes.utr is
    'The 12-digit UPI transaction reference the player submits after paying. Unique: one payment cannot back two passes.';
comment on column premium_passes.ends_on is
    'Last day the pass is valid, inclusive. Null for a founder pass, which does not end.';
