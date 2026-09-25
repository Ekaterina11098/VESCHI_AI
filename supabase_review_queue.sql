-- Run once in Supabase SQL Editor. Keep the secret key in server-side secrets only.
create table if not exists public.review_replies (
    feedback_id text primary key,
    answer text not null check (length(trim(answer)) > 0),
    state text not null default 'pending'
        check (state in ('pending', 'sending', 'sent', 'needs_check')),
    retry_at bigint not null default 0,
    attempts integer not null default 0,
    error text not null default '',
    created_at timestamptz not null default now()
);

create index if not exists review_replies_due_idx
    on public.review_replies (retry_at) where state = 'pending';
alter table public.review_replies enable row level security;
revoke all on public.review_replies from public, anon, authenticated;
grant select, insert, update on public.review_replies to service_role;

create or replace function public.enqueue_review_reply(
    p_feedback_id text, p_answer text, p_retry_at bigint
) returns jsonb language plpgsql set search_path = '' as $$
declare r public.review_replies;
begin
    if nullif(trim(p_feedback_id), '') is null or nullif(trim(p_answer), '') is null then
        raise exception 'ID and answer required';
    end if;
    insert into public.review_replies(feedback_id, answer, retry_at)
    values (p_feedback_id, p_answer, p_retry_at)
    on conflict (feedback_id) do nothing;
    select * into r from public.review_replies where feedback_id = p_feedback_id;
    return jsonb_build_object('state', r.state, 'retry_at', r.retry_at, 'error', r.error);
end;
$$;

create or replace function public.claim_review_reply(p_now bigint)
returns jsonb language plpgsql set search_path = '' as $$
declare r public.review_replies;
begin
    update public.review_replies as q
    set state = 'sending', attempts = attempts + 1
    where feedback_id = (
        select feedback_id from public.review_replies
        where state = 'pending' and retry_at <= p_now
        order by retry_at, created_at
        for update skip locked limit 1
    ) returning q.* into r;
    if not found then return null; end if;
    return jsonb_build_object('feedback_id', r.feedback_id, 'answer', r.answer);
end;
$$;

create or replace function public.finish_review_reply(
    p_feedback_id text, p_state text, p_retry_at bigint, p_error text
) returns boolean language plpgsql set search_path = '' as $$
begin
    if p_state not in ('pending', 'sent', 'needs_check') then
        raise exception 'Invalid state';
    end if;
    update public.review_replies
    set state = p_state, retry_at = p_retry_at, error = coalesce(p_error, '')
    where feedback_id = p_feedback_id and state = 'sending';
    return found;
end;
$$;

revoke execute on function public.enqueue_review_reply(text,text,bigint) from public, anon, authenticated;
revoke execute on function public.claim_review_reply(bigint) from public, anon, authenticated;
revoke execute on function public.finish_review_reply(text,text,bigint,text) from public, anon, authenticated;
grant execute on function public.enqueue_review_reply(text,text,bigint) to service_role;
grant execute on function public.claim_review_reply(bigint) to service_role;
grant execute on function public.finish_review_reply(text,text,bigint,text) to service_role;
