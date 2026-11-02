-- Bloom moves into Louvre project (own schema 'bloom'). Meridian Zero.
-- REQUIRED after running: Dashboard > Integrations > Data API > Settings > Exposed schemas: add 'bloom' and Save.
-- (notify pgrst only reloads the cache; it does not expose a schema.) Done on 2026-11-02.
begin;
create schema if not exists bloom;
grant usage on schema bloom to anon, authenticated, service_role;

create table bloom.services (
  id text primary key, cat text not null check (cat = any (array['lashes','brows','nails','pedi'])),
  name_ar text not null, name_en text not null, price integer not null check (price >= 0),
  minutes integer not null default 60, fill integer not null default 0, style boolean not null default false,
  active boolean not null default true, sort integer not null default 0);

create table bloom.booking_requests (
  id uuid primary key default gen_random_uuid(), created_at timestamptz not null default now(),
  services jsonb not null check (jsonb_typeof(services)='array' and jsonb_array_length(services) between 1 and 6),
  lash_style text check (lash_style = any (array['classic','hybrid','silk','volume'])),
  day date not null, time text not null check (time ~ '^[0-9]{1,2}$'),
  name text not null check (char_length(name) between 2 and 60),
  phone text not null check (phone ~ '^01[0-9]{9}$'),
  friend text check (friend is null or friend ~ '^01[0-9]{9}$'),
  first_visit boolean not null default false, note text check (note is null or char_length(note) <= 500),
  pay text not null check (pay = any (array['instapay','full','salon'])),
  total integer not null check (total between 0 and 20000), lang text check (lang = any (array['ar','en'])),
  status text not null default 'new' check (status = any (array['new','confirmed','arrived','noshow','cancelled'])),
  consent boolean not null default false, consent_at timestamptz, deposit_received boolean not null default false,
  paid_at timestamptz, request_id uuid, minutes integer);
-- Existing bookings are copied privately in the database (never committed to Git).
create unique index booking_requests_request_id_key on bloom.booking_requests (request_id) where request_id is not null;

insert into bloom.services select * from jsonb_populate_recordset(null::bloom.services, $j$[{"id": "p1", "cat": "lashes", "name_ar": "تعبئة رموش أسبوعية", "name_en": "Weekly lash refresh", "price": 250, "minutes": 40, "fill": 7, "style": false, "active": true, "sort": 1}, {"id": "p2", "cat": "lashes", "name_ar": "تعبئة رموش", "name_en": "Lash refill", "price": 250, "minutes": 60, "fill": 14, "style": false, "active": true, "sort": 2}, {"id": "p3", "cat": "lashes", "name_ar": "رموش شهرية", "name_en": "Monthly lashes", "price": 500, "minutes": 90, "fill": 21, "style": false, "active": true, "sort": 3}, {"id": "p4", "cat": "lashes", "name_ar": "رموش فول سيت", "name_en": "Lash full set", "price": 700, "minutes": 120, "fill": 21, "style": true, "active": true, "sort": 4}, {"id": "p5", "cat": "lashes", "name_ar": "رموش سيلك", "name_en": "Silk lashes", "price": 700, "minutes": 120, "fill": 21, "style": true, "active": true, "sort": 5}, {"id": "p6", "cat": "brows", "name_ar": "لامينيشن حواجب", "name_en": "Brow lamination", "price": 500, "minutes": 45, "fill": 42, "style": false, "active": true, "sort": 6}, {"id": "p8", "cat": "nails", "name_ar": "جل", "name_en": "Gel nails", "price": 450, "minutes": 60, "fill": 21, "style": false, "active": true, "sort": 7}, {"id": "p9", "cat": "nails", "name_ar": "إكس جل", "name_en": "X-gel nails", "price": 550, "minutes": 75, "fill": 21, "style": false, "active": true, "sort": 8}, {"id": "p10", "cat": "nails", "name_ar": "هارد جل", "name_en": "Hard gel", "price": 850, "minutes": 90, "fill": 21, "style": false, "active": true, "sort": 9}, {"id": "p11", "cat": "nails", "name_ar": "ماربل جل", "name_en": "Marble gel", "price": 550, "minutes": 75, "fill": 21, "style": false, "active": true, "sort": 10}, {"id": "p12", "cat": "nails", "name_ar": "أظافر صناعية", "name_en": "Fake nails", "price": 450, "minutes": 75, "fill": 21, "style": false, "active": true, "sort": 11}, {"id": "p13", "cat": "nails", "name_ar": "جل للقدم", "name_en": "Gel toes", "price": 400, "minutes": 45, "fill": 21, "style": false, "active": true, "sort": 12}, {"id": "p14", "cat": "pedi", "name_ar": "باديكير", "name_en": "Pedicure", "price": 200, "minutes": 45, "fill": 0, "style": false, "active": true, "sort": 13}, {"id": "p15", "cat": "pedi", "name_ar": "باديكير ديلوكس", "name_en": "Pedicure deluxe", "price": 300, "minutes": 60, "fill": 0, "style": false, "active": true, "sort": 14}, {"id": "p16", "cat": "pedi", "name_ar": "مانيكير + باديكير", "name_en": "Mani + pedi", "price": 500, "minutes": 90, "fill": 0, "style": false, "active": true, "sort": 15}, {"id": "p18", "cat": "lashes", "name_ar": "رموش سيلك فوليوم بريميوم", "name_en": "Premium silk volume set", "price": 1300, "minutes": 150, "fill": 21, "style": true, "active": true, "sort": 6}, {"id": "p19", "cat": "lashes", "name_ar": "تعبئة رموش بعد ١٤ يوم", "name_en": "Late lash refill (after 14 days)", "price": 350, "minutes": 75, "fill": 14, "style": false, "active": true, "sort": 7}, {"id": "p7", "cat": "brows", "name_ar": "ميكروبليدنج", "name_en": "Microblading", "price": 1200, "minutes": 120, "fill": 42, "style": false, "active": true, "sort": 7}, {"id": "p21", "cat": "lashes", "name_ar": "رموش + لامينيشن حواجب (كومبو)", "name_en": "Lash + brow lamination combo", "price": 1100, "minutes": 150, "fill": 21, "style": true, "active": true, "sort": 8}, {"id": "p22", "cat": "lashes", "name_ar": "رموش ميجا فوليوم (بريميوم)", "name_en": "Mega volume set (premium)", "price": 1800, "minutes": 180, "fill": 21, "style": true, "active": true, "sort": 6}]$j$::jsonb);

alter table bloom.services enable row level security;
alter table bloom.booking_requests enable row level security;
create policy "anyone reads active services" on bloom.services for select to anon, authenticated using (active);
create policy "anyone sends a booking request" on bloom.booking_requests for insert to anon
  with check (status='new' and deposit_received=false and paid_at is null and day >= (now() at time zone 'Africa/Cairo')::date and day <= (now() at time zone 'Africa/Cairo')::date + 60);
create policy "owner reads requests" on bloom.booking_requests for select to authenticated using ((auth.jwt() ->> 'email') = 'meridian.zero82@gmail.com');
create policy "owner updates requests" on bloom.booking_requests for update to authenticated using ((auth.jwt() ->> 'email') = 'meridian.zero82@gmail.com');

revoke all on bloom.services, bloom.booking_requests from anon, authenticated;
grant select on bloom.services to anon, authenticated;
grant insert on bloom.booking_requests to anon;
grant select, insert, update on bloom.booking_requests to authenticated;
grant all on all tables in schema bloom to service_role;
create or replace function bloom.bloom_check_request()
 returns trigger language plpgsql security definer set search_path to 'bloom'
as $function$
declare
  cairo_now timestamp := (now() at time zone 'Africa/Cairo');
  h int; s jsonb; sid text; p int; m int; tot int := 0; mins int := 0; fixed jsonb := '[]'::jsonb; taken int;
  offers jsonb := '{"o1":[600,135],"o2":[750,100],"o3":[550,120],"o4":[850,165],"o5":[850,105]}';
begin
  if tg_op = 'UPDATE' and new.day = old.day and new.time = old.time and new.services = old.services
     and new.minutes is not distinct from old.minutes and new.total is not distinct from old.total
     and (new.status = old.status or new.status not in ('new','confirmed','arrived')) then
    return new;
  end if;
  if new.time !~ '^[0-9]{1,2}$' then raise exception 'bloom: bad time'; end if;
  h := new.time::int;
  if h < 13 or h > 19 then raise exception 'bloom: outside opening hours'; end if;
  if tg_op = 'INSERT' and (new.day < cairo_now::date or new.day > cairo_now::date + 60) then raise exception 'bloom: bad day'; end if;
  if extract(dow from new.day) = 1 then raise exception 'bloom: closed on Monday'; end if;
  if tg_op = 'INSERT' and new.day = cairo_now::date and h <= extract(hour from cairo_now) then raise exception 'bloom: time already passed'; end if;
  if tg_op = 'UPDATE' and new.services = old.services then
    new.total := old.total; new.minutes := coalesce(old.minutes, 60); mins := new.minutes;
  else
  for s in select * from jsonb_array_elements(new.services) loop
    sid := s->>'id';
    if offers ? sid then p := (offers->sid->>0)::int; m := (offers->sid->>1)::int;
    else select price, minutes into p, m from services where id = sid and (active or tg_op = 'UPDATE');
      if not found then raise exception 'bloom: unknown service %', sid; end if;
    end if;
    tot := tot + p; mins := mins + m;
    fixed := fixed || jsonb_build_object('id', sid, 'name', left(coalesce(s->>'name',''),80), 'price', p);
  end loop;
  new.services := fixed; new.total := tot; new.minutes := mins;
  end if;
  if h*60 + mins > 20*60 then raise exception 'bloom: ends after closing'; end if;
  if tg_op = 'INSERT' then
    if (select count(*) from booking_requests where phone = new.phone and created_at > now() - interval '1 day') >= 3
      then raise exception 'bloom: too many requests from this number'; end if;
    if (select count(*) from booking_requests where created_at > now() - interval '10 minutes') >= 20
      then raise exception 'bloom: busy, try again later'; end if;
  end if;
  if new.status in ('new','confirmed','arrived') then
    perform pg_advisory_xact_lock(hashtext('bloom-day-' || new.day::text));
    select count(*) into taken from booking_requests b
     where b.day = new.day and b.status in ('new','confirmed','arrived') and b.id <> new.id
       and (b.time::int*60) < h*60 + mins and (b.time::int*60 + coalesce(b.minutes,60)) > h*60;
    if taken >= 1 then raise exception 'bloom: slot taken'; end if;
  end if;
  return new;
end $function$;

create or replace function bloom.bloom_taken_slots()
 returns table(day date, start_min integer, end_min integer)
 language sql stable security definer set search_path to 'bloom'
as $function$
  select day, time::int*60, time::int*60 + coalesce(minutes,60) from booking_requests
  where day >= (now() at time zone 'Africa/Cairo')::date and status in ('new','confirmed','arrived');
$function$;

revoke all on function bloom.bloom_check_request() from public, anon, authenticated;
revoke all on function bloom.bloom_taken_slots() from public;
grant execute on function bloom.bloom_taken_slots() to anon, authenticated, service_role;

create trigger bloom_check_request before insert or update on bloom.booking_requests
  for each row execute function bloom.bloom_check_request();
commit;

notify pgrst, 'reload schema';
