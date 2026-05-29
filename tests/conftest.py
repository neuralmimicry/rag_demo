import sys, os, types
import matplotlib

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Provide lightweight stubs for heavy/absent third-party modules so imports succeed
# holidays stub
try:
    import holidays  # type: ignore
except Exception:  # pragma: no cover - only used when dependency missing
    holidays = types.ModuleType('holidays')
    class _UK:
        def __contains__(self, d):
            return False
    UnitedKingdom = lambda: _UK()
    UnitedStates = lambda: _UK()
    Canada = lambda: _UK()
    Germany = lambda: _UK()
    France = lambda: _UK()
    def country_holidays(code):
        return _UK()
    holidays.UnitedKingdom = UnitedKingdom
    holidays.UnitedStates = UnitedStates
    holidays.Canada = Canada
    holidays.Germany = Germany
    holidays.France = France
    holidays.country_holidays = country_holidays
    sys.modules['holidays'] = holidays

# flask stub
try:
    import flask  # type: ignore
except Exception:  # pragma: no cover
    flask = types.ModuleType('flask')
    class Flask:
        def __init__(self, name):
            self.name = name
        def route(self, *a, **k):
            def deco(f):
                return f
            return deco
    def jsonify(obj):
        return obj
    class _Req:
        json = {}
    request = _Req()
    flask.Flask = Flask
    flask.request = request
    flask.jsonify = jsonify
    sys.modules['flask'] = flask

# fuzzywuzzy stub
try:
    from fuzzywuzzy import process  # type: ignore
except Exception:  # pragma: no cover
    fw = types.ModuleType('fuzzywuzzy')
    class _P:
        @staticmethod
        def extractOne(query, choices):
            return (query, 100)
    fw.process = _P()
    sys.modules['fuzzywuzzy'] = fw
    sys.modules['fuzzywuzzy.process'] = fw.process

# jira stub
try:
    from jira import JIRA  # type: ignore
except Exception:  # pragma: no cover
    jira_mod = types.ModuleType('jira')
    class JIRA:  # minimal stub
        def __init__(self, *a, **k):
            pass
    jira_mod.JIRA = JIRA
    sys.modules['jira'] = jira_mod

# werkzeug.security stub
try:
    from werkzeug.security import check_password_hash, generate_password_hash  # type: ignore # noqa: F401
except Exception:  # pragma: no cover
    werkzeug_mod = types.ModuleType('werkzeug')
    werkzeug_security = types.ModuleType('werkzeug.security')

    def generate_password_hash(value):
        return f"stub::{value}"

    def check_password_hash(hashed, value):
        return hashed == f"stub::{value}"

    werkzeug_security.generate_password_hash = generate_password_hash
    werkzeug_security.check_password_hash = check_password_hash
    werkzeug_mod.security = werkzeug_security
    sys.modules['werkzeug'] = werkzeug_mod
    sys.modules['werkzeug.security'] = werkzeug_security

# psycopg/psycopg_pool stubs
try:
    import psycopg  # type: ignore
except Exception:  # pragma: no cover
    psycopg = types.ModuleType('psycopg')
    rows_mod = types.ModuleType('psycopg.rows')
    rows_mod.dict_row = object()
    types_mod = types.ModuleType('psycopg.types')
    json_mod = types.ModuleType('psycopg.types.json')

    class Jsonb:  # minimal wrapper used by code paths in tests
        def __init__(self, value):
            self.value = value

    def _connect(*_args, **_kwargs):
        raise RuntimeError("psycopg is not installed in this test environment")

    json_mod.Jsonb = Jsonb
    types_mod.json = json_mod
    psycopg.connect = _connect
    psycopg.rows = rows_mod
    psycopg.types = types_mod
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows_mod
    sys.modules['psycopg.types'] = types_mod
    sys.modules['psycopg.types.json'] = json_mod

try:
    import psycopg_pool  # type: ignore
except Exception:  # pragma: no cover
    psycopg_pool = types.ModuleType('psycopg_pool')

    class ConnectionPool:  # minimal placeholder for imports
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def wait(self):
            return None

        def connection(self):
            raise RuntimeError("ConnectionPool stub does not provide real connections")

    psycopg_pool.ConnectionPool = ConnectionPool
    sys.modules['psycopg_pool'] = psycopg_pool

# matplotlib non-interactive backend
matplotlib.use('Agg')
# also ensure pyplot exists
try:
    import matplotlib.pyplot as plt  # noqa: F401
except Exception:  # pragma: no cover
    mpl = types.ModuleType('matplotlib.pyplot')
    def pie(*a, **k):
        return (None, None)
    def title(*a, **k):
        pass
    def savefig(*a, **k):
        pass
    def close(*a, **k):
        pass
    class _Ax:
        def pie(self, *a, **k):
            return pie(*a, **k)
        def axis(self, *a, **k):
            pass
    class _Fig:
        pass
    def subplots():
        return _Fig(), _Ax()
    mpl.pie = pie
    mpl.title = title
    mpl.savefig = savefig
    mpl.close = close
    mpl.subplots = subplots
    sys.modules['matplotlib.pyplot'] = mpl
