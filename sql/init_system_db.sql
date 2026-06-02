-- sql/init_system_db.sql
-- Oracle DDL for the SYSTEM_DB (oracle-ai-database-free-26ai).
-- Run as the SYSTEM_DB schema owner.  Safe to re-run: uses CREATE TABLE IF NOT EXISTS equivalent.
-- NOTE: Oracle does not support IF NOT EXISTS natively; use exception-block pattern.

-- ============================================================
-- 1. USERS  – authentication & RBAC
-- ============================================================
DECLARE
BEGIN
    EXECUTE IMMEDIATE '
        CREATE TABLE USERS (
            USER_ID       VARCHAR2(100)   NOT NULL,
            USERNAME      VARCHAR2(100)   NOT NULL,
            PASSWORD      VARCHAR2(255)   NOT NULL,  -- pbkdf2:sha256 hash; plaintext STRICTLY FORBIDDEN
            ROLE          VARCHAR2(50)    DEFAULT ''USER'',
            CREATED_AT    DATE            DEFAULT SYSDATE,
            CONSTRAINT PK_USERS        PRIMARY KEY (USER_ID),
            CONSTRAINT UQ_USERS_NAME   UNIQUE      (USERNAME),
            CONSTRAINT CK_USERS_ROLE  CHECK       (ROLE IN (''USER'',''ADMIN'',''TOPICADMIN''))
        )
    ';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;  -- -955 = table already exists
END;
/

-- ============================================================
-- 2. SEARCH_HISTORY  – keyword audit & autocomplete source
-- ============================================================
DECLARE
BEGIN
    EXECUTE IMMEDIATE '
        CREATE TABLE SEARCH_HISTORY (
            ID         NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            USER_ID    VARCHAR2(100),
            KEYWORD    VARCHAR2(500)   NOT NULL,
            CREATED_AT DATE            DEFAULT SYSDATE
        )
    ';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ============================================================
-- 3. SAVED_SEARCHES  – persistent saved query conditions with 30-day TTL
--    (The application deletes rows WHERE CREATED_AT < SYSDATE - 30 on each login.)
-- ============================================================
DECLARE
BEGIN
    EXECUTE IMMEDIATE '
        CREATE TABLE SAVED_SEARCHES (
            ID              NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            USER_ID         VARCHAR2(100)   NOT NULL,
            QUERY_JSON      CLOB            NOT NULL,
            CREATED_AT      DATE            DEFAULT SYSDATE
        )
    ';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ============================================================
-- 4. THEMES  – topic-collection metadata stored in Oracle
--    (Supplementary to the JSON file approach; provides FK-safe owner)
-- ============================================================
DECLARE
BEGIN
    EXECUTE IMMEDIATE '
        CREATE TABLE THEMES (
            THEME_ID    NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            THEME_NAME  VARCHAR2(100)   NOT NULL UNIQUE,
            TITLE       VARCHAR2(300)   NOT NULL,
            CREATED_BY  VARCHAR2(100),
            CREATED_AT  DATE            DEFAULT SYSDATE,
            UPDATED_AT  DATE            DEFAULT SYSDATE
        )
    ';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ============================================================
-- 5. AUDIT_LOGS  – immutable security audit trail
-- ============================================================
DECLARE
BEGIN
    EXECUTE IMMEDIATE '
        CREATE TABLE AUDIT_LOGS (
            LOG_ID      NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ACTION_TYPE VARCHAR2(50)    NOT NULL,   -- Login / Search / Download / Export / Admin ...
            USER_ID     VARCHAR2(100),
            IP_ADDRESS  VARCHAR2(45),               -- supports IPv6
            TARGET_ID   VARCHAR2(500),
            DETAILS     CLOB,
            LOG_TIME    DATE            DEFAULT SYSDATE
        )
    ';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- ============================================================
-- 6. USER_BOOKMARKS  – "我的書房" personal bookmark list
--    App logic enforces MAX 100 bookmarks per user before INSERT.
-- ============================================================
DECLARE
BEGIN
    EXECUTE IMMEDIATE '
        CREATE TABLE USER_BOOKMARKS (
            ID          NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            USER_ID     VARCHAR2(100)   NOT NULL,
            SYS_NO      VARCHAR2(200)   NOT NULL,
            DATA_TYPE   VARCHAR2(50)    NOT NULL,
            ADDED_AT    DATE            DEFAULT SYSDATE,
            CONSTRAINT UQ_BOOKMARK UNIQUE (USER_ID, SYS_NO)
        )
    ';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
DECLARE
BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX IDX_BM_USER ON USER_BOOKMARKS(USER_ID)';
EXCEPTION WHEN OTHERS THEN NULL; END;
/

-- ============================================================
-- 7. Indexes for common query patterns
-- ============================================================
DECLARE
BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX IDX_SRCH_HIST_USER ON SEARCH_HISTORY(USER_ID)';
EXCEPTION WHEN OTHERS THEN NULL; END;
/
DECLARE
BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX IDX_SRCH_HIST_KW   ON SEARCH_HISTORY(KEYWORD)';
EXCEPTION WHEN OTHERS THEN NULL; END;
/
DECLARE
BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX IDX_SAVED_USER      ON SAVED_SEARCHES(USER_ID)';
EXCEPTION WHEN OTHERS THEN NULL; END;
/
DECLARE
BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX IDX_SAVED_CREATED   ON SAVED_SEARCHES(CREATED_AT)';
EXCEPTION WHEN OTHERS THEN NULL; END;
/
DECLARE
BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX IDX_AUDIT_USER      ON AUDIT_LOGS(USER_ID)';
EXCEPTION WHEN OTHERS THEN NULL; END;
/
DECLARE
BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX IDX_AUDIT_TIME      ON AUDIT_LOGS(LOG_TIME)';
EXCEPTION WHEN OTHERS THEN NULL; END;
/

COMMIT;
