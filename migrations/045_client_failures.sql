-- Why a player's request failed, as their own browser saw it [A180].
--
-- "Couldn't reach the server" covers three different things -- a dropped connection, a
-- request that timed out, and a server that answered with an error -- and only the
-- browser can tell them apart: a request that never arrived leaves no trace on the
-- server at all, and Vercel keeps its own logs for about an hour. So the page records
-- each failure on the device and sends a batch the next time a request succeeds (sending
-- it while the connection is down would fail the same way). One row per batch.
--
-- `ok_count` is the number of requests that SUCCEEDED on that page since its last
-- report, so a failure count can be read as a rate rather than a bare number.
--
-- Operational, like rooms and accounts: nothing here comes from the archive. Holds no
-- personal data -- a page, a request path, timings and the browser's own network flags.
-- Rows older than 30 days are pruned by the route that inserts them.

create table client_failure_reports (
    report_id   bigserial primary key,
    received_at timestamptz not null default now(),
    page        text not null check (length(page) <= 100),
    ok_count    integer not null check (ok_count >= 0),
    failures    jsonb not null check (jsonb_typeof(failures) = 'array'
                                      and jsonb_array_length(failures) <= 50)
);

create index client_failure_reports_received_idx on client_failure_reports (received_at);

comment on table client_failure_reports is
    'Request failures as each player''s browser recorded them (A180): one row per batch, sent once a later request succeeds. failures is an array of {t, kind, method, path, status, ms, online, net, visible}; kind is timeout | network | server. Pruned after 30 days.';
