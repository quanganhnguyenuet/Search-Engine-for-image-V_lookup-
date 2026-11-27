-- Roles for V-Lookup: one profile row per user, and its role copied into every access token as
-- the "user_role" claim. The API (services/api/src/visualseek_api/core/auth.py) and the web proxy
-- read that claim; "admin" opens /admin and /api/v1/admin, anything else is a plain user.
--
-- Run once in the Supabase SQL Editor, then enable the hook:
--   Authentication -> Hooks -> Customize Access Token (JWT) Claims -> public.custom_access_token_hook
-- Make someone an admin (they must sign out and in again, or wait for the token to refresh):
--   update public.profiles set role = 'admin' where email = '<email>';

-- 1. Profiles
create table if not exists public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  email      text not null,
  full_name  text,
  role       text not null default 'user' check (role in ('user', 'admin')),
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- A user reads their own profile. There is no insert/update/delete policy, so nobody can change a
-- role through the public API; roles change only through SQL run by the project owner.
drop policy if exists "profiles: read own" on public.profiles;
create policy "profiles: read own"
  on public.profiles for select to authenticated
  using ((select auth.uid()) = id);

-- 2. A profile for every new user
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, email, full_name)
  values (new.id, new.email, new.raw_user_meta_data ->> 'full_name')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Users who signed up before this migration.
insert into public.profiles (id, email, full_name)
select id, coalesce(email, ''), raw_user_meta_data ->> 'full_name'
from auth.users
on conflict (id) do nothing;

-- 3. The hook that puts the role into the access token
create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
  claims    jsonb;
  user_role text;
begin
  select role into user_role from public.profiles where id = (event ->> 'user_id')::uuid;
  claims := event -> 'claims';
  claims := jsonb_set(claims, '{user_role}', to_jsonb(coalesce(user_role, 'user')));
  return jsonb_set(event, '{claims}', claims);
end;
$$;

grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook from authenticated, anon, public;
grant select on table public.profiles to supabase_auth_admin;
drop policy if exists "profiles: auth admin reads" on public.profiles;
create policy "profiles: auth admin reads"
  on public.profiles for select to supabase_auth_admin
  using (true);
