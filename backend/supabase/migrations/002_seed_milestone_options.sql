-- Seed milestone_options
-- These appear in the FE dropdown via GET /milestones/options

insert into milestone_options (name, description, product_type) values
  ('Auto-released on funding', 'M1: 30% disbursed automatically when investor funds the financing', null),
  ('Upload purchase invoice from sub-vendor', 'M2 (Invoice/PO): Proof that supplier purchased raw materials', null),
  ('Upload Surat Jalan or BAST', 'M3 (Invoice) / M4 (PO): Delivery proof signed by buyer', null),
  ('Upload QC report or production photos', 'M3 (PO only): Proof that production is complete', 'po')
on conflict (name) do nothing;
