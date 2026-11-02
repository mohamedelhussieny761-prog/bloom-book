-- Meridian Zero · Bloom Book server safety v3 (Crew Loop round 1, ChatGPT review on PR #3).
-- NOT YET APPLIED — the Captain runs it in the Supabase SQL Editor. Additive only: no data removed.
-- Fixes:
--  1. Offer durations now match the app menu: o1 = 135 min, o2 = 100 min (v2 had 85 and 150).
--  2. Two clients booking the same hour at the same moment: requests for one day now wait in line
--     (transaction lock per day), so the second one sees the first and is refused.
--  3. The same check runs when a booking's day, time, services or status is changed later.

create or replace function public.bloom_check_request() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  cairo_now timestamp := (now() at time zone 'Africa/Cairo');
  h int; s jsonb; sid text; p int; m int; tot int := 0; mins int := 0; fixed jsonb := '[]'::jsonb; taken int;
  offers jsonb := '{"o1":[600,135],"o2":[750,100],"o3":[550,120],"o4":[850,165],"o5":[850,105]}';
begin
  -- staff updates that do not move the booking or reactivate it need no new check
  if tg_op = 'UPDATE' and new.day = old.day and new.time = old.time and new.services = old.services
     and (new.status = old.status or new.status not in ('new','confirmed','arrived')) then
    return new;
  end if;
  if new.time !~ '^[0-9]{1,2}$' then raise exception 'bloom: bad time'; end if;
  h := new.time::int;
  if h < 13 or h > 19 then raise exception 'bloom: outside opening hours'; end if;
  if tg_op = 'INSERT' and (new.day < cairo_now::date or new.day > cairo_now::date + 60) then raise exception 'bloom: bad day'; end if;
  if extract(dow from new.day) = 1 then raise exception 'bloom: closed on Monday'; end if;
  if tg_op = 'INSERT' and new.day = cairo_now::date and h <= extract(hour from cairo_now) then raise exception 'bloom: time already passed'; end if;
  for s in select * from jsonb_array_elements(new.services) loop
    sid := s->>'id';
    if offers ? sid then p := (offers->sid->>0)::int; m := (offers->sid->>1)::int;
    else select price, minutes into p, m from services where id = sid and (active or tg_op = 'UPDATE');
      if not found then raise exception 'bloom: unknown service %', sid; end if;
    end if;
    tot := tot + p; mins := mins + m;
    fixed := fixed || jsonb_build_object('id', sid, 'name', left(coalesce(s->>'name',''),80), 'price', p);
  end loop;
  if h*60 + mins > 20*60 then raise exception 'bloom: ends after closing'; end if;
  new.services := fixed; new.total := tot; new.minutes := mins;
  if tg_op = 'INSERT' then
    if (select count(*) from booking_requests where phone = new.phone and created_at > now() - interval '1 day') >= 3
      then raise exception 'bloom: too many requests from this number'; end if;
    if (select count(*) from booking_requests where created_at > now() - interval '10 minutes') >= 20
      then raise exception 'bloom: busy, try again later'; end if;
  end if;
  if new.status in ('new','confirmed','arrived') then
    -- one booking per day at a time: concurrent requests for the same day queue here until the first commits
    perform pg_advisory_xact_lock(hashtext('bloom-day-' || new.day::text));
    select count(*) into taken from booking_requests b
     where b.day = new.day and b.status in ('new','confirmed','arrived') and b.id <> new.id
       and (b.time::int*60) < h*60 + mins and (b.time::int*60 + coalesce(b.minutes,60)) > h*60;
    if taken >= 1 then raise exception 'bloom: slot taken'; end if;
  end if;
  return new;
end $$;

drop trigger if exists bloom_check_request on public.booking_requests;
create trigger bloom_check_request before insert or update on public.booking_requests
  for each row execute function public.bloom_check_request();

-- correct the stored length of existing offer bookings made with the wrong v2 durations
update public.booking_requests br set minutes = x.m
from (select id, sum(case e->>'id' when 'o1' then 135 when 'o2' then 100 when 'o3' then 120 when 'o4' then 165 when 'o5' then 105
                                   else coalesce((select minutes from services sv where sv.id = e->>'id'),60) end)::int as m
      from booking_requests, jsonb_array_elements(services) e group by id) x
where br.id = x.id and br.services::text ~ '"o[1-5]"' and br.minutes is distinct from x.m;

revoke all on function public.bloom_check_request() from public, anon, authenticated;
