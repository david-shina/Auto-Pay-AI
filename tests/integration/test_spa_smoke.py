"""SPA smoke tests for the stripped-down bundle.

The frontend → backend wiring has been removed. The page renderers
read from a `data` namespace that returns placeholder content. The
new dataflow will replace `data.*` with real calls.

These tests pin the post-removal invariants so a re-introduction
of the old code (apiFetch, state.user mutation, polling, focus
listeners, etc.) is caught immediately.

If Node is unavailable, the e2e tests are skipped — they aren't a
hard dependency, just a correctness check.
"""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # tests/integration/.. → repo root
JS_PATH = REPO / "app" / "static" / "app.js"


def _get_spa_assets() -> tuple[str, str]:
    import os
    os.environ["TELEGRAM_BOT_TOKEN"] = ""  # disable bot init
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    return c.get("/").text, c.get("/static/app.js").text


NODE_SCRIPT = textwrap.dedent(r"""
    const html = process.env.SPA_HTML;
    const js = process.env.SPA_JS;
    if (!html || !js) { console.error('missing SPA_HTML/SPA_JS'); process.exit(2); }

    const stubEl = () => ({
        classList: { add(){}, remove(){}, toggle(){}, contains(){return false;} },
        append(){}, appendChild(){}, setAttribute(){}, removeAttribute(){},
        addEventListener(){}, remove(){}, removeChild(){},
        style:{}, dataset:{},
        innerHTML:'', textContent:'', value:'', disabled:false, files:[],
        querySelector(){return stubEl();},
        querySelectorAll(){return [];},
    });
    const document = {
        body: stubEl(), head: stubEl(),
        createElement: stubEl, createTextNode: (t) => ({ nodeType: 3, textContent: t }),
        querySelector(){return stubEl();}, querySelectorAll(){return [];},
        getElementById(){return stubEl();}, addEventListener(){},
        location: { hash: '', href: 'http://testserver/' },
    };
    const localStorage = { _:{},
        getItem(k){return this._[k] ?? null;},
        setItem(k,v){this._[k]=v;},
        removeItem(k){delete this._[k];},
        clear(){this._={};},
    };
    const fetch = async () => ({ ok:false, status:401, json: async()=>({detail:'no'}), headers:{get:()=>''} });
    const window = { location:{hash:'', href:'http://testserver/'}, addEventListener(){} };
    window.window = window;
    const MutationObserver = function(){ this.observe = () => {}; };
    const setTimeout = (fn,t)=>0; const clearTimeout=()=>{};
    const setInterval = (fn,t)=>0; const clearInterval = ()=>{};

    const onclickNames = new Set();
    for (const m of html.matchAll(/onclick="([a-zA-Z_][a-zA-Z0-9_]*)\(/g)) {
        onclickNames.add(m[1]);
    }

    const vm = require('vm');
    const ctx = { document, window, localStorage, fetch,
        MutationObserver, setTimeout, clearTimeout,
        setInterval, clearInterval, console };
    ctx.window = window; ctx.globalThis = window;
    try {
        vm.createContext(ctx);
        vm.runInContext(js, ctx);
    } catch (e) {
        console.error('bundle threw on load:', e.message);
        process.exit(3);
    }

    const errors = [];
    for (const name of onclickNames) {
        if (typeof ctx.window[name] !== 'function') {
            errors.push(`onclick="${name}()" is in HTML but window.${name} is not a function`);
        }
    }
    if (errors.length) {
        console.error('FAIL:');
        for (const e of errors) console.error('  ' + e);
        process.exit(1);
    }
    if (typeof ctx.window.routeTo === 'function') {
        ctx.window.routeTo('/dashboard');
        if (ctx.window.location.hash !== '#/dashboard') {
            console.error('routeTo did not set hash (got ' + JSON.stringify(ctx.window.location.hash) + ')');
            process.exit(1);
        }
    }
    console.log('OK — ' + onclickNames.size + ' onclick handlers, all wired');
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not on PATH")
def test_spa_buttons_are_wired_to_globals() -> None:
    html, js = _get_spa_assets()
    import os, subprocess, tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(NODE_SCRIPT)
        script_path = f.name
    try:
        env = os.environ.copy()
        env["SPA_HTML"] = html
        env["SPA_JS"] = js
        result = subprocess.run(["node", script_path], env=env, capture_output=True, text=True, timeout=20)
    finally:
        Path(script_path).unlink(missing_ok=True)
    if result.returncode != 0:
        pytest.fail(f"SPA smoke failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    assert "OK" in result.stdout


def test_spa_html_loads_without_404() -> None:
    """The HTML and JS are both served at the expected URLs."""
    import os
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200
    assert "id=\"app\"" in r.text
    r2 = c.get("/static/app.js")
    assert r2.status_code == 200


def test_spa_no_apiFetch_in_bundle() -> None:
    """apiFetch was the entire backend wiring. Its presence means
    someone re-introduced the old dataflow. We strip comments
    first so the explanatory comment block at the top of app.js
    doesn't count."""
    import re
    src = JS_PATH.read_text(encoding="utf-8")
    # Strip line comments.
    code = re.sub(r"//[^\n]*", "", src)
    # Strip block comments.
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    assert "apiFetch" not in code, (
        "app.js re-introduced `apiFetch` in code (not just a comment). "
        "The frontend-to-backend wiring has been removed; backend "
        "calls should go through the new dataflow (the `data` namespace)."
    )


def test_spa_no_backend_paths_in_bundle() -> None:
    """The bundle code should not reference any /api/v1/* path.
    We strip comments so the explanatory comment block doesn't
    count."""
    import re
    src = JS_PATH.read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", src)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    assert "/api/v1/" not in code, (
        "app.js code (not just a comment) contains /api/v1/ paths. "
        "The backend wiring is removed; data should flow through "
        "the `data` namespace."
    )


def test_spa_no_localStorage_auth_keys() -> None:
    """Auth was via localStorage tokens. With auth removed, no
    localStorage.getItem/setItem of access/refresh tokens."""
    src = JS_PATH.read_text(encoding="utf-8")
    for forbidden in ["ap_access_token", "ap_refresh_token", "STORAGE_KEYS"]:
        assert forbidden not in src, (
            f"app.js still references `{forbidden}`. The auth flow "
            "was removed along with the backend wiring."
        )


def test_spa_no_old_state_machine_for_balance() -> None:
    """The state-machine balance flow (setBalance / loadBalance /
    reRenderBalanceCards / BALANCE_STATUS) was tied to the old
    dataflow. Its re-introduction means someone is wiring back
    up the network polling/focus listeners we just removed.
    We strip comments first so the explanatory comment block
    at the top of app.js doesn't trigger a false positive."""
    import re
    src = JS_PATH.read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", src)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    for forbidden in [
        "BALANCE_STATUS",
        "setBalance(",
        "setBalanceLoading",
        "reRenderBalanceCards",
        "_WALLET_POLL_INTERVAL_MS",
        "loadBalance(",
        "startWalletPolling",
        "stopWalletPolling",
    ]:
        assert forbidden not in code, (
            f"app.js code (not just a comment) still references "
            f"`{forbidden}`. The state-machine balance flow is part "
            "of the old backend wiring. New dataflow should be wired "
            "through the `data` namespace."
        )


def test_spa_data_namespace_exists() -> None:
    """The new dataflow anchor: every page renderer reads from
    the `data` namespace. Its shape is:

        data = {
          user, wallet, dva, bills, recentBills,
          transactions, recentTxns,
          upload, topup, payBill, cancelBill,
          provision, linkCode, schedule, refresh,
        }
    """
    src = JS_PATH.read_text(encoding="utf-8")
    assert "const data = {" in src, (
        "app.js is missing `const data = { ... }`. The new "
        "dataflow anchors all page reads through the `data` namespace."
    )
    # All read-shape entries.
    for key in ["user", "wallet", "dva", "bills", "recentBills",
                "transactions", "recentTxns"]:
        assert f"{key}:" in src, (
            f"data namespace is missing the `{key}` reader. "
            f"Page renderers expect `data.{key}()` to return a promise."
        )
    # All action-shape entries (form submissions).
    for key in ["upload", "topup", "payBill", "cancelBill",
                "provision", "linkCode", "schedule", "refresh"]:
        assert f"{key}:" in src, (
            f"data namespace is missing the `{key}` action. "
            f"Form-submit handlers expect `data.{key}()` to exist."
        )


def test_spa_data_namespace_is_exposed_on_window() -> None:
    """window.data must be set so tests / devtools can introspect."""
    src = JS_PATH.read_text(encoding="utf-8")
    assert "window.data = data" in src, (
        "app.js is missing `window.data = data`. The data "
        "namespace must be exposed for inspection."
    )


def test_spa_inline_action_wrappers_exist() -> None:
    """The inline `onclick="..."` handlers in the markup call
    top-level functions. Those functions must exist and delegate
    to `data.*` so the markup can keep its inline onclick attrs."""
    src = JS_PATH.read_text(encoding="utf-8")
    wrappers = [
        "function doUpload()",
        "function doTopup()",
        "function payBillNow(",
        "function cancelBill(",
        "function provisionDva()",
        "function generateLinkCode()",
        "function submitSchedule()",
        "function manualRefreshDashboard()",
        "function doLogout()",
    ]
    for w in wrappers:
        assert w in src, (
            f"app.js is missing `{w}`. The inline onclick handlers "
            "in index.html reference this function; without it, the "
            "click would throw `function is not defined`."
        )


def test_spa_hash_router_routes_table_intact() -> None:
    """The hash router must still map all 7 pages."""
    src = JS_PATH.read_text(encoding="utf-8")
    for route in [
        "'#/login'",
        "'#/dashboard'",
        "'#/upload'",
        "'#/bills'",
        "'#/wallet'",
        "'#/telegram'",
        "'#/schedule'",
        "'#/404'",
    ]:
        assert route in src, (
            f"app.js is missing the route {route}. The hash router "
            "is the SPA's navigation backbone — without it, deep "
            "links and back/forward don't work."
        )


def test_spa_no_console_errors_on_load() -> None:
    """End-to-end in Node: load the bundle, no exceptions."""
    import os, subprocess, tempfile
    html, js = _get_spa_assets()
    node_script = textwrap.dedent("""
        const html = process.env.SPA_HTML;
        const js = process.env.SPA_JS;
        const stubEl = () => ({
            classList: { add(){}, remove(){}, toggle(){}, contains(){return false;} },
            append(){}, appendChild(){}, setAttribute(){}, removeAttribute(){},
            addEventListener(){}, remove(){}, removeChild(){},
            style:{}, dataset:{}, innerHTML:'', textContent:'', value:'', disabled:false, files:[],
            querySelector(){return stubEl();}, querySelectorAll(){return [];},
        });
        const document = {
            body: stubEl(), head: stubEl(),
            createElement: stubEl, createTextNode: (t) => ({ nodeType: 3, textContent: t }),
            querySelector(){return stubEl();}, querySelectorAll(){return [];},
            getElementById(){return stubEl();}, addEventListener(){},
            location: { hash: '', href: 'http://testserver/' },
        };
        const localStorage = { _:{}, getItem(){return null;}, setItem(){}, removeItem(){}, clear(){} };
    const window = { location:{_hash:'', get hash(){return this._hash;}, set hash(v){this._hash=v;}, href:'http://testserver/'}, addEventListener(){} };
        window.window = window;
        const MutationObserver = function(){ this.observe = () => {}; };
        const setTimeout = (fn,t)=>0; const clearTimeout=()=>{};
        const setInterval = (fn,t)=>0; const clearInterval = ()=>{};
        const vm = require('vm');
        const ctx = { document, window, localStorage,
            MutationObserver, setTimeout, clearTimeout,
            setInterval, clearInterval, console };
        ctx.window = window; ctx.globalThis = window;
        try { vm.createContext(ctx); vm.runInContext(js, ctx); console.log('OK'); }
        catch (e) { console.error('THROW:', e.message); process.exit(1); }
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(node_script)
        script_path = f.name
    try:
        env = os.environ.copy()
        env["SPA_HTML"] = html
        env["SPA_JS"] = js
        result = subprocess.run(["node", script_path], env=env, capture_output=True, text=True, timeout=20)
    finally:
        Path(script_path).unlink(missing_ok=True)
    if shutil.which("node") is None:
        pytest.skip("Node.js not on PATH")
    assert result.returncode == 0, (
        f"Bundle threw on load.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "OK" in result.stdout


def test_spa_format_helpers_kept() -> None:
    """The formatting helpers are used by the page renderers and
    by future dataflow code. They must remain."""
    src = JS_PATH.read_text(encoding="utf-8")
    for helper in [
        "function formatNaira(",
        "function escape_md(",
        "function formatDate(",
        "function formatLastSynced(",
        "function toast(",
        "function confirmDialog(",
    ]:
        assert helper in src, (
            f"app.js is missing `{helper}`. This is a UI helper used "
            "by page renderers; removing it will break the next dataflow."
        )
