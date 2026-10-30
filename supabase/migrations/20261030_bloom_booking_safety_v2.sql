-- Meridian Zero · Bloom Book server safety (Captain "fix approved" 30-10-2026).
-- APPLIED by the Captain in Supabase SQL Editor, 30-10-2026 09:48.
-- After applying: set serverV2:true in index.html CFG.
alter table public.booking_requests add column if not exists request_id uuid;
alter table public.booking_requests add column if not exists minutes integer;
create unique index if not exists booking_requests_request_id_key on public.booking_requests(request_id) where request_id is not null;

create or replace function public.bloom_check_request() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  cairo_now timestamp := (now() at time zone 'Africa/Cairo');
  h int; s jsonb; sid text; p int; m int; tot int := 0; mins int := 0; fixed jsonb := '[]'::jsonb; taken int;
  offers jsonb := '{"o1":[600,85],"o2":[750,150],"o3":[550,120],"o4":[850,165],"o5":[850,105]}';
begin
  if new.time !~ '^[0-9]{1,2}$' then raise exception 'bloom: bad time'; end if;
  h := new.time::int;
  if h < 13 or h > 19 then raise exception 'bloom: outside opening hours'; end if;
  if new.day < cairo_now::date or new.day > cairo_now::date + 60 then raise exception 'bloom: bad day'; end if;
  if extract(dow from new.day) = 1 then raise exception 'bloom: closed on Monday'; end if;
  if new.day = cairo_now::date and h <= extract(hour from cairo_now) then raise exception 'bloom: time already passed'; end if;
  for s in select * from jsonb_array_elements(new.services) loop
    sid := s->>'id';
    if offers ? sid then p := (offers->sid->>0)::int; m := (offers->sid->>1)::int;
    else select price, minutes into p, m from services where id = sid and active;
      if not found then raise exception 'bloom: unknown service %', sid; end if;
    end if;
    tot := tot + p; mins := mins + m;
    fixed := fixed || jsonb_build_object('id', sid, 'name', left(coalesce(s->>'name',''),80), 'price', p);
  end loop;
  if h*60 + mins > 20*60 then raise exception 'bloom: ends after closing'; end if;
  new.services := fixed; new.total := tot; new.minutes := mins;   -- prices always from the salon list, never the phone
  if (select count(*) from booking_requests where phone = new.phone and created_at > now() - interval '1 day') >= 3
    then raise exception 'bloom: too many requests from this number'; end if;
  if (select count(*) from booking_requests where created_at > now() - interval '10 minutes') >= 20
    then raise exception 'bloom: busy, try again later'; end if;
  select count(*) into taken from booking_requests b
   where b.day = new.day and b.status in ('new','confirmed','arrived')
     and (b.time::int*60) < h*60 + mins and (b.time::int*60 + coalesce(b.minutes,60)) > h*60;
  if taken >= 1 then raise exception 'bloom: slot taken'; end if;   -- staff capacity: 1 client at a time
  return new;
end $$;
drop trigger if exists bloom_check_request on public.booking_requests;
create trigger bloom_check_request before insert on public.booking_requests
  for each row execute function public.bloom_check_request();

create or replace function public.bloom_taken_slots() returns table(day date, start_min int, end_min int)
language sql stable security definer set search_path = public as $$
  select day, time::int*60, time::int*60 + coalesce(minutes,60) from booking_requests
  where day >= (now() at time zone 'Africa/Cairo')::date and status in ('new','confirmed','arrived');
$$;
revoke all on function public.bloom_taken_slots() from public;
grant execute on function public.bloom_taken_slots() to anon, authenticated;
revoke all on function public.bloom_check_request() from public, anon, authenticated;
