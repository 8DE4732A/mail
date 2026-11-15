PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS folder (
  id            INTEGER PRIMARY KEY,
  account_key   TEXT    NOT NULL,
  name          TEXT    NOT NULL,
  delimiter     TEXT,
  role          TEXT,
  uidvalidity   INTEGER,
  uidnext       INTEGER,
  highestmodseq INTEGER,
  last_sync_at  TEXT,
  sync_enabled  INTEGER NOT NULL DEFAULT 1,
  UNIQUE (account_key, name)
);

CREATE TABLE IF NOT EXISTS message (
  id              INTEGER PRIMARY KEY,
  account_key     TEXT    NOT NULL,
  folder_id       INTEGER NOT NULL REFERENCES folder(id) ON DELETE CASCADE,
  uid             INTEGER NOT NULL,
  message_id      TEXT,
  in_reply_to     TEXT,
  references_json TEXT,
  subject         TEXT    NOT NULL DEFAULT '',
  from_json       TEXT    NOT NULL DEFAULT '[]',
  to_json         TEXT    NOT NULL DEFAULT '[]',
  cc_json         TEXT    NOT NULL DEFAULT '[]',
  reply_to_json   TEXT    NOT NULL DEFAULT '[]',
  date_utc        TEXT    NOT NULL,
  size            INTEGER,
  flags_json      TEXT    NOT NULL DEFAULT '[]',
  is_seen         INTEGER NOT NULL DEFAULT 0,
  is_flagged      INTEGER NOT NULL DEFAULT 0,
  is_deleted      INTEGER NOT NULL DEFAULT 0,
  has_attachments INTEGER NOT NULL DEFAULT 0,
  snippet         TEXT    NOT NULL DEFAULT '',
  body_state      TEXT    NOT NULL DEFAULT 'none'
                  CHECK (body_state IN ('none','fetching','ready','error')),
  created_at      TEXT    NOT NULL,
  UNIQUE (folder_id, uid)
);

CREATE INDEX IF NOT EXISTS idx_message_list
  ON message (folder_id, is_deleted, date_utc DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_message_unread
  ON message (folder_id, is_seen);
CREATE INDEX IF NOT EXISTS idx_message_account
  ON message (account_key, date_utc DESC);
CREATE INDEX IF NOT EXISTS idx_message_msgid
  ON message (account_key, message_id);

CREATE TABLE IF NOT EXISTS body (
  message_id  INTEGER PRIMARY KEY REFERENCES message(id) ON DELETE CASCADE,
  text_body   TEXT,
  html_text   TEXT,
  raw_eml     BLOB,
  fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachment (
  id           INTEGER PRIMARY KEY,
  message_id   INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  part_id      TEXT    NOT NULL,
  filename     TEXT,
  content_type TEXT,
  size         INTEGER,
  content_id   TEXT,
  local_path   TEXT,
  content      BLOB,
  state        TEXT    NOT NULL DEFAULT 'remote'
               CHECK (state IN ('remote','downloaded','missing')),
  UNIQUE (message_id, part_id)
);

CREATE TABLE IF NOT EXISTS draft (
  id                  INTEGER PRIMARY KEY,
  account_key         TEXT    NOT NULL,
  kind                TEXT    NOT NULL
                      CHECK (kind IN ('new','reply','reply_all','forward')),
  reply_to_message_id INTEGER REFERENCES message(id) ON DELETE SET NULL,
  to_json             TEXT    NOT NULL DEFAULT '[]',
  cc_json             TEXT    NOT NULL DEFAULT '[]',
  bcc_json            TEXT    NOT NULL DEFAULT '[]',
  subject             TEXT    NOT NULL DEFAULT '',
  body_text           TEXT    NOT NULL DEFAULT '',
  status              TEXT    NOT NULL DEFAULT 'editing'
                      CHECK (status IN ('editing','queued','sent','failed')),
  created_at          TEXT    NOT NULL,
  updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_attachment (
  id           INTEGER PRIMARY KEY,
  draft_id     INTEGER NOT NULL REFERENCES draft(id) ON DELETE CASCADE,
  filename     TEXT    NOT NULL,
  content_type TEXT    NOT NULL,
  size         INTEGER NOT NULL,
  content      BLOB    NOT NULL
);

CREATE TABLE IF NOT EXISTS operation (
  id              INTEGER PRIMARY KEY,
  account_key     TEXT    NOT NULL,
  kind            TEXT    NOT NULL
                  CHECK (kind IN ('send','flag','move','delete','append_sent')),
  message_id      INTEGER REFERENCES message(id) ON DELETE SET NULL,
  draft_id        INTEGER REFERENCES draft(id) ON DELETE SET NULL,
  payload_json    TEXT    NOT NULL DEFAULT '{}',
  mime_blob       BLOB,
  status          TEXT    NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','done','failed','unknown')),
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  last_error      TEXT,
  created_at      TEXT    NOT NULL,
  updated_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operation_claim
  ON operation (status, next_attempt_at, id);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
  subject,
  sender,
  recipients,
  body,
  tokenize = 'trigram'
);
