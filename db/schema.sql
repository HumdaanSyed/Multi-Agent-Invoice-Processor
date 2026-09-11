-- Phase 4: Output/database schema.
-- Run this in the Supabase SQL editor (Project > SQL Editor > New query).
--
-- Storage bucket: this file only covers Postgres tables. Also create a
-- Storage bucket named `invoice-pdfs` (Project > Storage > New bucket,
-- private) for `upload_pdf()` in invoice_agent/db.py to write to.

create table if not exists invoices (
    id bigint generated always as identity primary key,
    invoice_number text not null,
    invoice_date date not null,
    vendor_name text not null,
    bill_to text not null,
    subtotal numeric(12, 2) not null,
    tax numeric(12, 2) not null,
    total numeric(12, 2) not null,
    due_date date,
    currency text not null,
    pdf_storage_path text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint invoices_vendor_number_unique unique (vendor_name, invoice_number)
);

create table if not exists line_items (
    id bigint generated always as identity primary key,
    invoice_id bigint not null references invoices (id) on delete cascade,
    description text not null,
    quantity numeric(12, 2) not null,
    unit_price numeric(12, 2) not null,
    amount numeric(12, 2) not null
);

create index if not exists idx_line_items_invoice_id on line_items (invoice_id);

-- Keep updated_at current on every upsert.
create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists invoices_set_updated_at on invoices;
create trigger invoices_set_updated_at
    before update on invoices
    for each row
    execute function set_updated_at();

-- Phase 8.6: durable export ledger, replacing the local exports/invoices.csv
-- file. Deliberately append-only - no unique constraint, unlike `invoices`
-- above. Re-running/correcting the same invoice (a resume that reaches
-- output() again) inserts a *new* row rather than upserting one, so the
-- ledger stays a true audit trail of every successful write, not a
-- deduplicated snapshot. Never derive this table's contents from `invoices`
-- (see invoice_agent/db.py's insert_export_row and REVIEW.md).
create table if not exists invoice_exports (
    id bigint generated always as identity primary key,
    written_at timestamptz not null default now(),
    thread_id text not null,
    invoice_number text not null,
    invoice_date date not null,
    vendor_name text not null,
    bill_to text not null,
    subtotal numeric(12, 2) not null,
    tax numeric(12, 2) not null,
    total numeric(12, 2) not null,
    due_date date,
    currency text not null,
    line_items jsonb not null default '[]'::jsonb
);

-- GET /export.csv orders by written_at; GET /export/status wants the most
-- recent row cheaply.
create index if not exists idx_invoice_exports_written_at on invoice_exports (written_at);
