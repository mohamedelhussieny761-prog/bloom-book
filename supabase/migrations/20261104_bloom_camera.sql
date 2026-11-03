-- Bloom camera AI: hourly counts from the shop PC agent. Meridian Zero.
-- Video only, no sound, no faces stored here: only numbers per camera per hour.
-- The agent writes through bloom.camera_ingest() with its own secret token;
-- nobody can read these tables through the public API.

create table if not exists bloom.camera_agents (
  id text primary key,                       -- e.g. 'shop-pc'
  token_hash text not null,                  -- sha256 of the agent token (token itself never stored)
  active boolean not null default true,
  last_seen timestamptz
);

create table if not exists bloom.camera_hourly (
  day date not null,
  hour smallint not null check (hour between 0 and 23),
  camera text not null check (char_length(camera) between 1 and 40),
  people_max smallint not null default 0 check (people_max between 0 and 50),
  people_avg numeric(5,2) not null default 0 check (people_avg between 0 and 50),
  busy_minutes smallint not null default 0 check (busy_minutes between 0 and 60),
  offline_minutes smallint not null default 0 check (offline_minutes between 0 and 60),
  after_hours_events smallint not null default 0 check (after_hours_events between 0 and 60),
  updated_at timestamptz not null default now(),
  primary key (day, hour, camera)
);

alter table bloom.camera_agents enable row level security;
alter table bloom.camera_hourly enable row level security;
revoke all on bloom.camera_agents, bloom.camera_hourly from anon, authenticated;

-- One call per upload: token + array of hourly rows (max 200).
create or replace function bloom.camera_ingest(p_agent text, p_token text, p_rows jsonb)
 returns integer language plpgsql security definer set search_path to 'bloom'
as $$
declare r jsonb; n int := 0; ok boolean;
begin
  select exists(select 1 from camera_agents a where a.id = p_agent and a.active
                and a.token_hash = encode(extensions.digest(p_token, 'sha256'), 'hex')) into ok;
  if not ok then raise exception 'bloom: camera agent not allowed'; end if;
  if jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) > 200 then
    raise exception 'bloom: bad camera rows'; end if;
  for r in select * from jsonb_array_elements(p_rows) loop
    insert into camera_hourly as c (day, hour, camera, people_max, people_avg, busy_minutes, offline_minutes, after_hours_events, updated_at)
    values ((r->>'day')::date, (r->>'hour')::smallint, left(r->>'camera',40),
            coalesce((r->>'people_max')::smallint,0), coalesce((r->>'people_avg')::numeric,0),
            coalesce((r->>'busy_minutes')::smallint,0), coalesce((r->>'offline_minutes')::smallint,0),
            coalesce((r->>'after_hours_events')::smallint,0), now())
    on conflict (day, hour, camera) do update set
      people_max = excluded.people_max, people_avg = excluded.people_avg,
      busy_minutes = excluded.busy_minutes, offline_minutes = excluded.offline_minutes,
      after_hours_events = excluded.after_hours_events, updated_at = now();
    n := n + 1;
  end loop;
  update camera_agents set last_seen = now() where id = p_agent;
  return n;
end $$;

revoke all on function bloom.camera_ingest(text, text, jsonb) from public;
grant execute on function bloom.camera_ingest(text, text, jsonb) to anon;
notify pgrst, 'reload schema';

-- Register the shop PC (run once) with the FINGERPRINT that --setup prints (it is already the sha256; the token stays on the PC):
--   insert into bloom.camera_agents (id, token_hash) values ('shop-pc', '<FINGERPRINT>');
