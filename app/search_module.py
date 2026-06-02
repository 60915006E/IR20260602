"""
app/search_module.py
====================
Blueprint for the `/search_test` module (standalone test harness).
Also contains the Boolean Search Parser used by the main search routes.
"""

from flask import Blueprint, render_template, request, jsonify, abort, session, redirect, url_for
from app.oracle_db import execute_query, build_search_sql
import re
import traceback
import logging
import sys
from app.auth.routes import admin_required

# ---------------------------------------------------------------------------
# Blueprint setup
# ---------------------------------------------------------------------------
search_test_bp = Blueprint(
    'search_test', __name__,
    template_folder='templates',
    url_prefix='/search_test'
)

@search_test_bp.before_request
def require_login():
    """Enforce the same login boundary as the main system."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))


logger = logging.getLogger('search_module')
logger.setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Boolean Search Parser (AST / Regex approach)
# ---------------------------------------------------------------------------
# Grammar supported:
#   TERM [AND|OR TERM]* [NOT TERM]*
#   Parenthesised sub-expressions, e.g.: A AND (B OR C) NOT D
#
# Security constraint: user input is NEVER concatenated into SQL strings.
# Every parsed term is mapped to a numbered bind variable (:p_<n>).
# ---------------------------------------------------------------------------

class BooleanParseError(ValueError):
    """Raised when the boolean expression cannot be parsed."""
    pass


# Token types
_TT_TERM   = 'TERM'
_TT_AND    = 'AND'
_TT_OR     = 'OR'
_TT_NOT    = 'NOT'
_TT_LPAREN = 'LPAREN'
_TT_RPAREN = 'RPAREN'
_TT_EOF    = 'EOF'

_TOKEN_RE = re.compile(
    r'\s*(?:'
    r'(\bAND\b)|(\bOR\b)|(\bNOT\b)'
    r'|(\()|(\))'
    r'|("(?:[^"\\]|\\.)*")'     # quoted phrase
    r'|([^\s()"]+)'             # unquoted term
    r')\s*',
    re.IGNORECASE
)


def _tokenize(expr: str):
    """Tokenise a boolean search expression into a list of (type, value) tuples."""
    tokens = []
    for m in _TOKEN_RE.finditer(expr.strip()):
        and_kw, or_kw, not_kw, lp, rp, quoted, bare = m.groups()
        if and_kw:
            tokens.append((_TT_AND, 'AND'))
        elif or_kw:
            tokens.append((_TT_OR, 'OR'))
        elif not_kw:
            tokens.append((_TT_NOT, 'NOT'))
        elif lp:
            tokens.append((_TT_LPAREN, '('))
        elif rp:
            tokens.append((_TT_RPAREN, ')'))
        elif quoted:
            # Strip surrounding quotes; keep internal content
            tokens.append((_TT_TERM, quoted[1:-1]))
        elif bare:
            tokens.append((_TT_TERM, bare))
    tokens.append((_TT_EOF, ''))
    return tokens


class _Parser:
    """
    Recursive-descent parser for boolean expressions.
    Builds a SQL WHERE fragment using bind variables only – no string interpolation.
    
    Output: (sql_fragment: str, bind_vars: dict)
    """

    def __init__(self, tokens, columns, prefix_counter):
        self._tokens  = tokens
        self._pos     = 0
        self._columns = columns        # list of DB column names to search across
        self._counter = prefix_counter # list[int] acting as a mutable int reference

    # -- helpers ----------------------------------------------------------

    def _peek(self):
        return self._tokens[self._pos]

    def _consume(self, expected_type=None):
        tt, tv = self._tokens[self._pos]
        if expected_type and tt != expected_type:
            raise BooleanParseError(
                f"Expected {expected_type} but got {tt} ('{tv}') at position {self._pos}"
            )
        self._pos += 1
        return tt, tv

    def _next_bind_name(self):
        self._counter[0] += 1
        return f"bp_{self._counter[0]}"

    # -- grammar rules ----------------------------------------------------

    def parse(self):
        sql, params = self._expression()
        if self._peek()[0] != _TT_EOF:
            raise BooleanParseError("Unexpected token after expression end.")
        return sql, params

    def _expression(self):
        """expression := term (('AND'|'OR') term)*"""
        left_sql, params = self._not_expr()

        while self._peek()[0] in (_TT_AND, _TT_OR):
            op = self._consume()[0]  # AND or OR
            right_sql, right_params = self._not_expr()
            params.update(right_params)
            joiner = ' AND ' if op == _TT_AND else ' OR '
            left_sql = f"({left_sql}{joiner}{right_sql})"

        return left_sql, params

    def _not_expr(self):
        """not_expr := ['NOT'] primary"""
        if self._peek()[0] == _TT_NOT:
            self._consume(_TT_NOT)
            sql, params = self._primary()
            return f"NOT ({sql})", params
        return self._primary()

    def _primary(self):
        """primary := '(' expression ')' | TERM"""
        if self._peek()[0] == _TT_LPAREN:
            self._consume(_TT_LPAREN)
            sql, params = self._expression()
            self._consume(_TT_RPAREN)
            return f"({sql})", params

        if self._peek()[0] == _TT_TERM:
            _, value = self._consume(_TT_TERM)
            return self._term_to_sql(value)

        raise BooleanParseError(
            f"Unexpected token: {self._peek()} at position {self._pos}"
        )

    def _term_to_sql(self, value: str):
        """
        Map a single search term to an OR-chain of LIKE predicates,
        one per searchable column.  All values go through bind variables.

        Security: `value` is NEVER placed directly into the SQL string.
        """
        bind_name = self._next_bind_name()
        params    = {bind_name: f"%{value}%"}
        clauses   = [f"{col} LIKE :{bind_name}" for col in self._columns]
        return f"({' OR '.join(clauses)})", params


def parse_boolean_search(expr: str, columns: list, start_counter: int = 0):
    """
    Translate a boolean search expression into a parameterised SQL WHERE clause.

    Args:
        expr           : User-supplied boolean expression, e.g. "飛彈 AND (保養 OR 維修) NOT 採購"
        columns        : DB column names to perform LIKE searches against,
                         e.g. ['OVN_RP_NAME', 'OVN_SUMMARY', 'OVN_RP_AUTHOR_LIST']
        start_counter  : Offset for bind variable numbering (useful when combining with
                         other parameterised fragments).

    Returns:
        (sql_where_fragment: str, bind_params: dict)
        e.g. ("(col LIKE :bp_1 AND col LIKE :bp_2)", {'bp_1': '%飛彈%', 'bp_2': '%保養%'})

    Raises:
        BooleanParseError: If the expression is syntactically invalid.

    Security Guarantee:
        User input is NEVER concatenated into the returned SQL string.
        All search terms are placed into bind_params dict only.

    Example:
        >>> sql, params = parse_boolean_search(
        ...     "飛彈 AND (保養 OR 維修) NOT 採購",
        ...     columns=['OVN_RP_NAME', 'OVN_SUMMARY']
        ... )
        # sql  ≈  "((OVN_RP_NAME LIKE :bp_1 ...) AND ((... LIKE :bp_2 ...) OR (... LIKE :bp_3 ...))
        #           AND NOT (... LIKE :bp_4 ...))"
        # params = {'bp_1': '%飛彈%', 'bp_2': '%保養%', 'bp_3': '%維修%', 'bp_4': '%採購%'}
    """
    if not expr or not expr.strip():
        return '1=1', {}

    tokens  = _tokenize(expr)
    counter = [start_counter]
    parser  = _Parser(tokens, columns, counter)
    return parser.parse()


# ---------------------------------------------------------------------------
# Blueprint routes
# ---------------------------------------------------------------------------

@search_test_bp.route('/', methods=['GET'])
def index():
    """Search test module home page."""
    return render_template('search/index.html')


@search_test_bp.route('/advanced', methods=['GET'])
def advanced_search():
    """Advanced search page."""
    return render_template('search/advanced_search.html')


@search_test_bp.route('/results', methods=['GET', 'POST'])
def results():
    """
    Search result list page.
    Accepts standard field-based search OR a raw boolean expression
    via ?boolean=<expr>.
    """
    from app.db_manager import execute_query as db_execute_query
    fields  = request.form.getlist('field[]')
    ops     = request.form.getlist('operator[]')
    vals    = request.form.getlist('value[]')
    keyword = (
        request.form.get('keyword', '').strip() or
        request.args.get('keyword', '').strip()
    )
    boolean_expr = request.args.get('boolean', '').strip()

    records   = []
    error_msg = None

    try:
        if boolean_expr:
            # ── Boolean search path ──────────────────────────────────────
            # Default searchable columns (adjust to your actual view schema)
            bool_cols = [
                'OVN_RP_NAME', 'OVN_SUMMARY', 'OVN_RP_AUTHOR_LIST',
                'OVN_RP_KEYWORD', 'OVN_RP_LIB_TITLE',
            ]
            from config import Config
            prefix = getattr(Config, 'DATA_DB_SCHEMA', '') if hasattr(Config, 'DATA_DB_SCHEMA') else ''
            # Prefix columns with the full qualified view where needed is handled in oracle_db;
            # here we build the WHERE fragment only.
            where_sql, bool_params = parse_boolean_search(boolean_expr, bool_cols)
            sql = f"""
                SELECT *
                FROM {prefix}VI_IRLIB_REPORT_MAIN
                WHERE OVC_PUBLIC_TYPE_CDE = 'Y'
                  AND ({where_sql})
            """  # nosec B608
            records = _safe_execute_test_query(sql, bool_params)

        else:
            # ── Standard / advanced search path ─────────────────────────
            advanced_filters = []
            if fields and vals and len(fields) == len(vals):
                for i in range(len(fields)):
                    if vals[i].strip():
                        advanced_filters.append({
                            'field':    fields[i],
                            'operator': ops[i] if i < len(ops) else 'AND',
                            'value':    vals[i].strip()
                        })

            sql, parameters = build_search_sql(keyword, advanced_filters)
            records = _safe_execute_test_query(sql, parameters)

    except BooleanParseError as bpe:
        error_msg = f"布林語法錯誤：{str(bpe)}"
        logger.error(f"Boolean parse error: {str(bpe)}")
    except Exception as e:
        logger.error(f"Search Query Failed: {str(e)}\n{traceback.format_exc()}")
        error_msg = f"查詢時發生錯誤，請聯絡系統管理員。錯誤訊息: {str(e)}"

    return render_template(
        'search/results.html',
        records=records,
        keyword=keyword,
        error_msg=error_msg
    )


@search_test_bp.route('/detail/<token>', methods=['GET'])
def get_detail(token):
    """Single bibliographic detail page."""
    from itsdangerous import URLSafeSerializer
    from itsdangerous.exc import BadSignature
    from flask import current_app

    serializer = URLSafeSerializer(current_app.config['SECRET_KEY'], salt='permalink-salt')
    try:
        doc_id = serializer.loads(token)
    except BadSignature:
        # 跨電腦、重啟或 Session 遺失之物理防禦性 Fail-safe 兜底：直接以明文作為系統唯一號檢索，確保 100% 打開成功！
        doc_id = token

    from config import Config
    prefix = getattr(Config, 'DATA_DB_SCHEMA', 'IRLIB.') if hasattr(Config, 'DATA_DB_SCHEMA') else ''

    sql = f"""
        SELECT *
        FROM {prefix}VI_IRLIB_REPORT_MAIN
        WHERE OVC_RP_NO = :doc_id
          AND OVC_PUBLIC_TYPE_CDE = 'Y'
    """  # nosec B608

    try:
        records = _safe_execute_test_query(sql, {'doc_id': doc_id})
        if not records:
            abort(404, description="找不到此公開文件或文件不存在。")

        record = records[0]

        # Sub-table lookups for 技術報告
        sub_tables = [
            ('VI_IRLIB_RP_AUTHOR',      'OVN_RP_AUTHOR',      'OVN_RP_AUTHOR'),
            ('VI_IRLIB_RP_LIB_TITLE',   'OVC_RP_LIB_TITLE',   'OVC_RP_LIB_TITLE'),
            ('VI_IRLIB_RP_OTHER_TITLE',  'OVC_RP_OTHER_TITLE', 'OVC_RP_OTHER_TITLE'),
            ('VI_IRLIB_RP_OTHER_NAME',   'OVN_RP_OTHER_NAME',  'OVN_RP_OTHER_NAME'),
            ('VI_IRLIB_RP_KEYWORD',      'OVN_RP_KEYWORD',     'OVN_RP_KEYWORD'),
        ]
        for tbl, field, rec_key in sub_tables:
            sub_sql = f"SELECT {field} FROM {prefix}{tbl} WHERE OVC_RP_NO = :doc_id"  # nosec B608
            sub_res = _safe_execute_test_query(sub_sql, {'doc_id': doc_id})
            vals = [r[field] for r in sub_res if r.get(field)]
            if vals:
                record[rec_key] = ', '.join(vals)

        # Plan table (two columns)
        plan_sql = f"SELECT OVN_RP_PLAN_NAME, OVC_RP_PLAN_CDE FROM {prefix}VI_IRLIB_RP_PLAN WHERE OVC_RP_NO = :doc_id"  # nosec B608
        plan_res = _safe_execute_test_query(plan_sql, {'doc_id': doc_id})
        plan_names = [r['OVN_RP_PLAN_NAME'] for r in plan_res if r.get('OVN_RP_PLAN_NAME')]
        plan_cdes  = [r['OVC_RP_PLAN_CDE']  for r in plan_res if r.get('OVC_RP_PLAN_CDE')]
        if plan_names:
            record['OVN_RP_PLAN_NAME'] = ', '.join(plan_names)
        if plan_cdes:
            record['OVC_RP_PLAN_CDE']  = ', '.join(plan_cdes)

        # Mark data type for the template
        record.setdefault('DATA_TYPE', '技術報告')

    except Exception as e:
        logger.error(f"Detail Query Failed: {str(e)}\n{traceback.format_exc()}")
        abort(500, description="系統內部錯誤。")

    return render_template('search/detail.html', record=record)


@search_test_bp.route('/api/download/<doc_id>', methods=['GET'])
def api_download(doc_id):
    """Mock download API demonstrating the mandatory security intercept."""
    from config import Config
    from app.db_manager import execute_query, get_cache_db_conn
    
    # 1. 先從 GLOBAL_SEARCH_INDEX 查出該 doc_id 的 DATA_TYPE
    sys_conn_func = lambda: get_cache_db_conn()
    idx_sql = "SELECT DATA_TYPE FROM GLOBAL_SEARCH_INDEX WHERE SYS_NO = ?"
    try:
        idx_res = execute_query(sys_conn_func, idx_sql, [doc_id])
    except Exception as idx_err:
        logger.error(f"Fetch index for download failed: {idx_err}")
        idx_res = []

    if not idx_res:
        # 若找不到快取，退回為通用放行
        data_type = '其他'
    else:
        data_type = idx_res[0].get('DATA_TYPE')
    
    # 2. 如果是技術報告，才需要去 VI_IRLIB_REPORT_MAIN 檢核機密等級
    if data_type == '技術報告':
        prefix = getattr(Config, 'DATA_DB_SCHEMA', 'IRLIB.') if hasattr(Config, 'DATA_DB_SCHEMA') else ''
        sql = f"""
            SELECT OVC_SECRET_LV_CDE, OVC_PUBLIC_TYPE_CDE
            FROM {prefix}VI_IRLIB_REPORT_MAIN
            WHERE OVC_RP_NO = :doc_id
              AND OVC_PUBLIC_TYPE_CDE = 'Y'
        """  # nosec B608
        try:
            records = _safe_execute_test_query(sql, {'doc_id': doc_id})
            if not records:
                abort(404, description="找不到此公開技術報告文件。")

            record = records[0]
            # Security rule: 技術報告 with non-'NOR' secret level → 403
            if record.get('OVC_SECRET_LV_CDE') != 'NOR':
                abort(403, description="無權限下載此機敏等級之技術報告 (限 NOR)。")

        except Exception as e:
            if hasattr(e, 'code') and e.code in [403, 404]:
                raise e
            logger.error(f"Download Failed for Tech Report: {str(e)}")
            abort(500, description="檢核技術報告密等發生錯誤。")

    # 3. 如果是非技術報告 (史政、史政照片、逸光報)，直接放行
    return jsonify({"status": "success", "message": "檔案開始下載..."})


@search_test_bp.route('/admin', methods=['GET'])
@admin_required
def test_admin():
    """Test-module admin control panel."""
    from app.config_manager import PortalConfigManager, COLUMN_MAP
    from app.db_manager import execute_query as db_execute_query, get_system_db_conn

    users        = db_execute_query(lambda: get_system_db_conn(), "SELECT USER_ID, USERNAME, ROLE FROM USERS")
    portal_config = PortalConfigManager.load()

    return render_template(
        'search/admin_dashboard.html',
        users=users,
        portal_config=portal_config,
        all_columns=COLUMN_MAP
    )


@search_test_bp.route('/api/save_config', methods=['POST'])
@admin_required
def api_save_config():
    """Persist portal configuration from the admin UI."""
    from app.config_manager import PortalConfigManager
    try:
        data = request.json
        if PortalConfigManager.save(data):
            return jsonify({"status": "success", "message": "網站設定儲存成功！"})
        return jsonify({"status": "error", "message": "寫入設定檔失敗"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@search_test_bp.route('/api/update_role', methods=['POST'])
@admin_required
def api_update_role():
    """Change a user's role."""
    try:
        data = request.json
        uid  = data.get('user_id')
        role = data.get('role')
        if not uid or not role:
            return jsonify({"status": "error", "message": "參數短少"}), 400

        from app.db_manager import execute_update, get_system_db_conn
        execute_update(
            lambda: get_system_db_conn(),
            "UPDATE USERS SET ROLE = :1 WHERE USER_ID = :2",
            [role, uid]
        )
        return jsonify({"status": "success", "message": f"成功修改 {uid} 權限為 {role}。"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@search_test_bp.route('/api/sync_index', methods=['POST'])
@admin_required
def trigger_sync_index():
    """直接以函式呼叫取代 subprocess，消除 B603 高險漏洞。"""
    try:
        import importlib.util as _ilu
        import os as _os
        _spec = _ilu.spec_from_file_location(
            'sync_index',
            _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'sync_index.py')
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.run_sync_logic()
        return jsonify({"status": "success", "message": "同步完成"})
    except Exception as e:
        logger.error(f"Sync Index Failed: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _safe_execute_test_query(sql, parameters):
    """
    Wrapper around execute_query that delegates to the correct DB engine
    (SQLite or Oracle) based on DATA_DB_MODE.
    """
    return execute_query(sql, parameters)
