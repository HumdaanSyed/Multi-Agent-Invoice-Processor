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
