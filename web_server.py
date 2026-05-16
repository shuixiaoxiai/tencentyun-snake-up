import hashlib
import json
import random
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = BASE_DIR / "data"
DATA_DIR = DATA_ROOT / "tasks"
ADMIN_CONFIG_PATH = DATA_ROOT / "admin_config.json"
ADMIN_IDS_PATH = DATA_ROOT / "admin_ids.json"
STATIC_DIR = BASE_DIR / "web_static"

BEIJING_TZ = timezone(timedelta(hours=8))
SECKILL_HOURS = (10, 15)
TOKEN_REFRESH_BEFORE_SECONDS = 5 * 60
SERVER_TIME_CHECK_INTERVAL_SECONDS = 1
KEEPALIVE_MIN_SECONDS = 150
KEEPALIVE_MAX_SECONDS = 210
KEEPALIVE_FAILURE_LIMIT = 3
REGION_IDS = [1, 4, 8]
DEFAULT_MAX_CONCURRENT_TASKS = 2
VALID_ID_PREFIXES = ("aixiaoxi", "aicailan")
ACTIVE_TASK_STATUSES = {"created", "waiting_login", "logged_in", "waiting_seckill", "running"}
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "xshy00000"
ADMIN_SESSION_COOKIE = "tx_admin_session"

LOGIN_URL = (
    "https://cloud.tencent.com/login?s_url=https%3A%2F%2Fcloud.tencent.com%2Fact%2Fpro%2Fdouble12-2025"
    "%3FfromSource%3Dgwzcw.10216579.10216579.10216579%26utm_medium%3Dcpc%26utm_id%3Dgwzcw.10216579.10216579.10216579"
    "%26msclkid%3D9d471e943d2d142808a4771f328779e6"
)
ACTIVITY_URL = "https://cloud.tencent.com/act/pro/double12-2025"
REFERER_URL = (
    "https://cloud.tencent.com/act/pro/featured-202604?fromSource=gwzcw.10216579.10216579.10216579"
    "&utm_medium=cpc&utm_id=gwzcw.10216579.10216579.10216579&msclkid=6b370ba9f89c1d21e93a6225d46c8044"
    "&page=spring2026&s_source=https%3A%2F%2Fcloud.tencent.com%2Fact%2Fpro%2Fdouble12-2025"
)

CHECK_DATA = {
    "activity_id": 162634773874417,
    "goods": [{"act_id": 1784747698901873, "region_id": REGION_IDS}],
    "preview": 0,
}

BASE_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "referer": REFERER_URL,
}

app = FastAPI(title="Tencent Cloud Seckill Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

tasks_lock = threading.Lock()
running_tasks: dict[str, threading.Thread] = {}
task_cancel_events: dict[str, threading.Event] = {}
admin_sessions: set[str] = set()


class CreateTaskRequest(BaseModel):
    user_id: str


class AdminConfigRequest(BaseModel):
    max_concurrent_tasks: int


class GenerateIdsRequest(BaseModel):
    count: int


class BatchStatusRequest(BaseModel):
    user_ids: list[str]


class AdminLoginRequest(BaseModel):
    username: str
    password: str


def now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]


def get_task_dir(user_id: str) -> Path:
    return DATA_DIR / hash_user_id(user_id)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def is_admin_authenticated(request: Request) -> bool:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    return bool(token and token in admin_sessions)


def require_admin(request: Request) -> None:
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="请先登录管理员账号")


def read_admin_config() -> dict[str, Any]:
    config = read_json(ADMIN_CONFIG_PATH, {})
    if not isinstance(config, dict):
        config = {}
    return {
        "max_concurrent_tasks": int(config.get("max_concurrent_tasks", DEFAULT_MAX_CONCURRENT_TASKS)),
    }


def write_admin_config(config: dict[str, Any]) -> dict[str, Any]:
    max_concurrent_tasks = int(config.get("max_concurrent_tasks", DEFAULT_MAX_CONCURRENT_TASKS))
    if max_concurrent_tasks < 1:
        raise HTTPException(status_code=400, detail="并发数必须大于等于 1")
    normalized = {"max_concurrent_tasks": max_concurrent_tasks}
    write_json(ADMIN_CONFIG_PATH, normalized)
    return normalized


def read_generated_ids() -> dict[str, Any]:
    data = read_json(ADMIN_IDS_PATH, {"ids": {}})
    if not isinstance(data, dict):
        data = {"ids": {}}
    ids = data.get("ids")
    if isinstance(ids, list):
        ids = {value: {"created_at": None} for value in ids if isinstance(value, str)}
    if not isinstance(ids, dict):
        ids = {}
    return {"ids": ids}


def write_generated_ids(data: dict[str, Any]) -> None:
    write_json(ADMIN_IDS_PATH, data)


def is_generated_id(user_id: str) -> bool:
    return user_id in read_generated_ids().get("ids", {})


def is_legal_user_id(user_id: str) -> bool:
    return user_id.startswith(VALID_ID_PREFIXES) or is_generated_id(user_id)


def generate_user_ids(count: int) -> list[str]:
    if count not in {10, 50}:
        raise HTTPException(status_code=400, detail="只支持生成 10 或 50 个 id")
    data = read_generated_ids()
    ids = data["ids"]
    generated = []
    while len(generated) < count:
        value = f"tx-{secrets.token_hex(4)}"
        if value in ids:
            continue
        ids[value] = {"created_at": now_iso()}
        generated.append(value)
    write_generated_ids(data)
    return generated


def mark_generated_id_used(user_id: str) -> None:
    data = read_generated_ids()
    item = data.get("ids", {}).get(user_id)
    if isinstance(item, dict):
        item["used_at"] = item.get("used_at") or now_iso()
        write_generated_ids(data)


def count_active_tasks() -> int:
    if not DATA_DIR.exists():
        return 0
    count = 0
    for path in DATA_DIR.glob("*/state.json"):
        try:
            state = read_json(path, {})
        except Exception:
            continue
        if state.get("status") in ACTIVE_TASK_STATUSES:
            count += 1
    return count


def task_status_for_user_id(user_id: str) -> dict[str, Any]:
    task_dir = get_task_dir(user_id)
    state = read_json(state_path(task_dir), {})
    if not state:
        return {
            "user_id": user_id,
            "legal": is_legal_user_id(user_id),
            "exists": False,
            "status": "not_found",
            "message": "未找到任务",
        }
    state = with_dynamic_remaining(state)
    return {
        "user_id": user_id,
        "legal": is_legal_user_id(user_id),
        "exists": True,
        "status": state.get("status"),
        "message": state.get("message"),
        "next_seckill_time": state.get("next_seckill_time"),
        "remaining_seconds": state.get("remaining_seconds"),
        "updated_at": state.get("updated_at"),
    }


def state_path(task_dir: Path) -> Path:
    return task_dir / "state.json"


def cookies_path(task_dir: Path) -> Path:
    return task_dir / "cookies.json"


def result_path(task_dir: Path) -> Path:
    return task_dir / "result.json"


def qr_path(task_dir: Path) -> Path:
    return task_dir / "qr.png"


def get_cancel_event(user_id: str) -> threading.Event:
    with tasks_lock:
        event = task_cancel_events.get(user_id)
        if event is None:
            event = threading.Event()
            task_cancel_events[user_id] = event
        return event


def is_task_cancelled(user_id: str) -> bool:
    event = task_cancel_events.get(user_id)
    return bool(event and event.is_set())


def mark_task_cancelled(user_id: str) -> dict[str, Any]:
    get_cancel_event(user_id).set()
    return update_state(
        user_id,
        status="canceled",
        message="抢购已取消",
        remaining_seconds=0,
    )


def update_state(user_id: str, **changes: Any) -> dict[str, Any]:
    task_dir = get_task_dir(user_id)
    state = read_json(state_path(task_dir), {})
    state.update(changes)
    state["updated_at"] = now_iso()
    write_json(state_path(task_dir), state)
    return state


def capture_qr_area(page: Any, path: Path) -> None:
    selectors = ["canvas", "img", "[class*='qr']", "[class*='QR']", "[id*='qr']"]
    candidates = []
    for frame in page.frames:
        for selector in selectors:
            locator = frame.locator(selector)
            try:
                count = min(locator.count(), 20)
            except Exception:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    box = item.bounding_box(timeout=500)
                except Exception:
                    continue
                if not box:
                    continue
                width = box["width"]
                height = box["height"]
                if width < 120 or height < 120 or width > 420 or height > 420:
                    continue
                ratio = width / height
                if not 0.75 <= ratio <= 1.25:
                    continue
                square_score = abs(width - height)
                size_score = abs(((width + height) / 2) - 230)
                candidates.append((square_score + size_score, box))

    viewport = page.viewport_size or {"width": 1280, "height": 900}
    if candidates:
        _, box = min(candidates, key=lambda item: item[0])
        padding = 36
        x = max(0, box["x"] - padding)
        y = max(0, box["y"] - padding)
        right = min(viewport["width"], box["x"] + box["width"] + padding)
        bottom = min(viewport["height"], box["y"] + box["height"] + padding)
        clip = {"x": x, "y": y, "width": right - x, "height": bottom - y}
    else:
        size = min(360, viewport["width"], viewport["height"])
        clip = {
            "x": max(0, viewport["width"] * 0.18),
            "y": max(0, viewport["height"] * 0.22),
            "width": size,
            "height": size,
        }

    page.screenshot(path=str(path), clip=clip)


def with_dynamic_remaining(state: dict[str, Any]) -> dict[str, Any]:
    next_timestamp = state.get("next_seckill_timestamp")
    if next_timestamp and state.get("status") in {"logged_in", "waiting_seckill", "running"}:
        current_timestamp = int(datetime.now(BEIJING_TZ).timestamp() * 1000)
        state = dict(state)
        state["remaining_seconds"] = max(0, (int(next_timestamp) - current_timestamp) // 1000)
    return state


def get_cookie_value(cookies: list[dict[str, Any]], name: str) -> str | None:
    for cookie in reversed(cookies):
        if cookie.get("name") == name and cookie.get("value"):
            return cookie["value"]
    return None


def calc_csrf_token(skey_value: str) -> str:
    hash_val = 5381
    for ch in skey_value:
        hash_val += (hash_val << 5) + ord(ch)
        hash_val &= 2147483647
    return str(hash_val)


def create_session(cookies: list[dict[str, Any]]) -> requests.Session:
    session = requests.Session()
    for cookie in cookies:
        session.cookies.set(
            cookie.get("name", ""),
            cookie.get("value", ""),
            domain=cookie.get("domain", ""),
            path=cookie.get("path", "/"),
        )
    return session


def build_session_and_headers(cookies: list[dict[str, Any]]) -> tuple[requests.Session, dict[str, str]]:
    skey = get_cookie_value(cookies, "skey")
    if not skey:
        raise RuntimeError("cookies中未找到skey")
    headers = dict(BASE_HEADERS)
    headers["x-csrf-token"] = calc_csrf_token(skey)
    return create_session(cookies), headers


def next_keepalive_delay() -> int:
    return random.randint(KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS)


def refresh_cookies_from_browser(
    user_id: str,
    page: Any,
    context: Any,
    *,
    strong: bool = False,
) -> list[dict[str, Any]]:
    wait_until = "networkidle" if strong else "domcontentloaded"
    page.goto(ACTIVITY_URL, wait_until=wait_until, timeout=30000)
    if not strong:
        page.wait_for_timeout(random.randint(2000, 5000))
    cookies = context.cookies()
    if not get_cookie_value(cookies, "skey") or not get_cookie_value(cookies, "uin"):
        raise RuntimeError("浏览器登录态缺少skey或uin")
    write_json(cookies_path(get_task_dir(user_id)), cookies)
    update_state(user_id, last_cookie_refresh_at=now_iso(), browser_alive=True)
    return cookies


def get_server_time() -> int | None:
    response = requests.head(ACTIVITY_URL, timeout=10)
    server_time = response.headers.get("Date")
    if not server_time:
        return None
    dt = datetime.strptime(server_time, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
    beijing_time = dt.astimezone(BEIJING_TZ)
    return int(beijing_time.timestamp() * 1000)


def get_next_seckill_time(current_timestamp_ms: int) -> tuple[datetime, int]:
    current_dt = datetime.fromtimestamp(current_timestamp_ms / 1000, BEIJING_TZ)
    for hour in SECKILL_HOURS:
        target_dt = current_dt.replace(hour=hour, minute=0, second=0, microsecond=0)
        if current_dt <= target_dt:
            return target_dt, int(target_dt.timestamp() * 1000)
    tomorrow = current_dt + timedelta(days=1)
    target_dt = tomorrow.replace(hour=SECKILL_HOURS[0], minute=0, second=0, microsecond=0)
    return target_dt, int(target_dt.timestamp() * 1000)


def buy_now(session: requests.Session, headers: dict[str, str], region_id: int) -> dict[str, Any] | None:
    do_data = {
        "activity_id": 162634773874417,
        "agent_channel": {
            "fromChannel": "",
            "fromSales": "",
            "isAgentClient": False,
            "fromUrl": REFERER_URL,
        },
        "business": {"id": 22755, "from": "lightningDeals"},
        "goods": [
            {
                "act_id": 1784747698901873,
                "type": "bundle_budget_mc_lg4_01",
                "goods_param": {
                    "BlueprintId": "LINUX_UNIX",
                    "area": 1,
                    "ddocUnionConnect": 0,
                    "goodsNum": 1,
                    "imageId": "lhbp-eqora508",
                    "scenario": "0",
                    "timeSpanUnit": "12m",
                    "zone": "",
                    "regionId": region_id,
                    "type": "bundle_budget_mc_lg4_01",
                },
            }
        ],
        "preview": 0,
    }
    try:
        resp = session.post(
            "https://act-api.cloud.tencent.com/dianshi/do-goods",
            json=do_data,
            headers=headers,
            timeout=10,
        )
        return {"region_id": region_id, "http_status": resp.status_code, "body": resp.json()}
    except Exception as exc:
        return {"region_id": region_id, "error": str(exc)}


def buy_now_concurrent(session: requests.Session, headers: dict[str, str]) -> dict[str, Any]:
    results = []
    with ThreadPoolExecutor(max_workers=len(REGION_IDS)) as executor:
        futures = [executor.submit(buy_now, session, headers, rid) for rid in REGION_IDS]
        for future in futures:
            result = future.result()
            results.append(result)
            body = result.get("body") if isinstance(result, dict) else None
            if isinstance(body, dict) and body.get("code") == 0:
                return {"success": True, "results": results, "winner": result}
    return {"success": False, "results": results}


def run_task(user_id: str) -> None:
    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            run_login(user_id, page, context)
            state = read_json(state_path(get_task_dir(user_id)), {})
            if not is_task_cancelled(user_id) and state.get("status") == "logged_in":
                run_seckill(user_id, page, context)
    except Exception as exc:
        state = read_json(state_path(get_task_dir(user_id)), {})
        if state.get("status") not in {"success", "failed", "canceled"}:
            update_state(user_id, status="failed", message=f"任务流程失败: {exc}", browser_alive=False)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        update_state(user_id, browser_alive=False)
        with tasks_lock:
            running_tasks.pop(user_id, None)


def run_login(user_id: str, page: Any, context: Any) -> None:
    task_dir = get_task_dir(user_id)
    cancel_event = get_cancel_event(user_id)
    update_state(user_id, status="waiting_login", message="等待扫码登录", browser_alive=True)
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

    for _ in range(10):
        if cancel_event.is_set():
            mark_task_cancelled(user_id)
            return
        try:
            capture_qr_area(page, qr_path(task_dir))
            update_state(user_id, qr_ready=True, message="二维码已生成，请使用另一台设备摄像头扫码登录")
            break
        except Exception:
            time.sleep(1)

    login_deadline = time.monotonic() + 10 * 60
    while time.monotonic() < login_deadline:
        if cancel_event.is_set():
            mark_task_cancelled(user_id)
            return
        cookies = context.cookies()
        if get_cookie_value(cookies, "skey") and get_cookie_value(cookies, "uin"):
            write_json(cookies_path(task_dir), cookies)
            update_state(
                user_id,
                status="logged_in",
                message="登录成功，浏览器会话将保持到抢购结束",
                browser_alive=True,
                last_cookie_refresh_at=now_iso(),
            )
            return
        try:
            capture_qr_area(page, qr_path(task_dir))
        except PlaywrightTimeoutError:
            pass
        time.sleep(2)

    update_state(user_id, status="failed", message="登录超时，请重新创建任务")


def run_seckill(user_id: str, page: Any, context: Any) -> None:
    task_dir = get_task_dir(user_id)
    try:
        cancel_event = get_cancel_event(user_id)
        if cancel_event.is_set():
            mark_task_cancelled(user_id)
            return
        current_time = get_server_time()
        if current_time is None:
            raise RuntimeError("无法获取腾讯云服务器时间")

        seckill_dt, seckill_timestamp = get_next_seckill_time(current_time)
        token_refresh_timestamp = seckill_timestamp - TOKEN_REFRESH_BEFORE_SECONDS * 1000
        update_state(
            user_id,
            status="waiting_seckill",
            message="等待下一场抢购",
            next_seckill_time=seckill_dt.strftime("%Y-%m-%d %H:%M:%S"),
            next_seckill_timestamp=seckill_timestamp,
            token_refresh_time=datetime.fromtimestamp(token_refresh_timestamp / 1000, BEIJING_TZ).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )

        session = None
        headers = None
        token_ready = False
        keepalive_failures = 0
        delay = next_keepalive_delay()
        next_keepalive_at = time.monotonic() + delay
        update_state(
            user_id,
            browser_alive=True,
            next_cookie_refresh_at=datetime.fromtimestamp(time.time() + delay, BEIJING_TZ).isoformat(),
        )

        while True:
            if cancel_event.is_set():
                mark_task_cancelled(user_id)
                return
            current_time = get_server_time()
            if current_time is None:
                time.sleep(SERVER_TIME_CHECK_INTERVAL_SECONDS)
                continue

            remaining_seconds = max(0, (seckill_timestamp - current_time) // 1000)
            update_state(user_id, remaining_seconds=remaining_seconds)

            if not token_ready and time.monotonic() >= next_keepalive_at:
                try:
                    refresh_cookies_from_browser(user_id, page, context)
                    keepalive_failures = 0
                    delay = next_keepalive_delay()
                    next_keepalive_at = time.monotonic() + delay
                    update_state(
                        user_id,
                        message="登录态已保活刷新",
                        next_cookie_refresh_at=datetime.fromtimestamp(time.time() + delay, BEIJING_TZ).isoformat(),
                    )
                except Exception as exc:
                    keepalive_failures += 1
                    update_state(user_id, message=f"登录态保活刷新失败({keepalive_failures}/3): {exc}")
                    if keepalive_failures >= KEEPALIVE_FAILURE_LIMIT:
                        raise RuntimeError("登录态保活连续失败，请重新扫码")
                    delay = next_keepalive_delay()
                    next_keepalive_at = time.monotonic() + delay

            if not token_ready and current_time >= token_refresh_timestamp:
                cookies = refresh_cookies_from_browser(user_id, page, context, strong=True)
                session, headers = build_session_and_headers(cookies)
                token_ready = True
                update_state(user_id, message="抢购前登录态和x-csrf-token已刷新")

            if current_time >= seckill_timestamp:
                cookies = refresh_cookies_from_browser(user_id, page, context, strong=True)
                session, headers = build_session_and_headers(cookies)
                update_state(user_id, status="running", message="正在执行抢购", remaining_seconds=0)
                result = buy_now_concurrent(session, headers)
                write_json(result_path(task_dir), result)
                failed_by_login = any(
                    isinstance(item, dict)
                    and isinstance(item.get("body"), dict)
                    and item["body"].get("code") == "NOT-LOGINED"
                    for item in result.get("results", [])
                )
                update_state(
                    user_id,
                    status="success" if result.get("success") else "failed",
                    message=(
                        "抢购成功"
                        if result.get("success")
                        else "登录态校验失败，请重新扫码"
                        if failed_by_login
                        else "抢购失败"
                    ),
                    result=result,
                )
                return

            time.sleep(SERVER_TIME_CHECK_INTERVAL_SECONDS)
    except Exception as exc:
        update_state(user_id, status="failed", message=f"抢购流程失败: {exc}")


def sanitize_user_id(user_id: str) -> str:
    value = user_id.strip()
    if not value:
        raise HTTPException(status_code=400, detail="user_id不能为空")
    if "/" in value or "\\" in value:
        raise HTTPException(status_code=400, detail="user_id不能包含路径分隔符")
    if len(value) > 120:
        raise HTTPException(status_code=400, detail="user_id过长")
    return value


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", response_class=HTMLResponse)
def admin_index(request: Request) -> Response:
    if not is_admin_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin_login.html")


@app.post("/api/admin/login")
def admin_login(payload: AdminLoginRequest, response: Response) -> dict[str, Any]:
    if not (
        secrets.compare_digest(payload.username, ADMIN_USERNAME)
        and secrets.compare_digest(payload.password, ADMIN_PASSWORD)
    ):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = secrets.token_urlsafe(32)
    admin_sessions.add(token)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=12 * 60 * 60,
    )
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if token:
        admin_sessions.discard(token)
    response.delete_cookie(ADMIN_SESSION_COOKIE)
    return {"ok": True}


@app.post("/api/tasks")
def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    user_id = sanitize_user_id(payload.user_id)
    if not is_legal_user_id(user_id):
        raise HTTPException(status_code=403, detail="该 id 未授权，请先从管理员处获取 id")

    task_dir = get_task_dir(user_id)
    if state_path(task_dir).exists():
        state = read_json(state_path(task_dir), {})
        if state.get("status") == "failed":
            shutil.rmtree(task_dir)
        else:
            raise HTTPException(status_code=409, detail="该 id 已存在，请使用查询功能查看当前任务")

    config = read_admin_config()
    active_count = count_active_tasks()
    if active_count >= config["max_concurrent_tasks"]:
        raise HTTPException(status_code=429, detail=f"下一场抢购并发已满，当前上限为 {config['max_concurrent_tasks']}")

    state = {
        "user_id": user_id,
        "task_id": hash_user_id(user_id),
        "status": "created",
        "message": "任务已创建",
        "qr_ready": False,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    write_json(state_path(task_dir), state)
    mark_generated_id_used(user_id)

    get_cancel_event(user_id).clear()
    thread = threading.Thread(target=run_task, args=(user_id,), daemon=True)
    with tasks_lock:
        running_tasks[user_id] = thread
    thread.start()
    return read_task_state(user_id)


@app.post("/api/tasks/{user_id}/cancel")
def cancel_task(user_id: str) -> dict[str, Any]:
    user_id = sanitize_user_id(user_id)
    task_dir = get_task_dir(user_id)
    state = read_json(state_path(task_dir))
    if not state:
        raise HTTPException(status_code=404, detail="未找到任务")
    if state.get("status") in {"success", "failed", "canceled"}:
        return with_dynamic_remaining(state)
    return mark_task_cancelled(user_id)


@app.post("/api/admin/clear")
def clear_all_tasks(request: Request) -> dict[str, Any]:
    require_admin(request)
    with tasks_lock:
        for event in task_cancel_events.values():
            event.set()
        running_tasks.clear()
        task_cancel_events.clear()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "message": "所有本地任务数据已清除"}


@app.get("/api/admin/config")
def get_admin_config(request: Request) -> dict[str, Any]:
    require_admin(request)
    config = read_admin_config()
    return {
        **config,
        "active_tasks": count_active_tasks(),
        "valid_prefixes": list(VALID_ID_PREFIXES),
    }


@app.post("/api/admin/config")
def update_admin_config(payload: AdminConfigRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    config = write_admin_config({"max_concurrent_tasks": payload.max_concurrent_tasks})
    return {
        **config,
        "active_tasks": count_active_tasks(),
        "valid_prefixes": list(VALID_ID_PREFIXES),
    }


@app.get("/api/admin/ids")
def list_admin_ids(request: Request) -> dict[str, Any]:
    require_admin(request)
    ids = read_generated_ids().get("ids", {})
    return {
        "ids": [{"id": key, **value} for key, value in sorted(ids.items()) if isinstance(value, dict)],
        "count": len(ids),
    }


@app.post("/api/admin/ids/generate")
def create_admin_ids(payload: GenerateIdsRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    ids = generate_user_ids(payload.count)
    return {"ids": ids, "count": len(ids)}


@app.post("/api/admin/status")
def batch_task_status(payload: BatchStatusRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    user_ids = []
    seen = set()
    for raw_user_id in payload.user_ids:
        user_id = sanitize_user_id(raw_user_id)
        if user_id in seen:
            continue
        seen.add(user_id)
        user_ids.append(user_id)
    return {"items": [task_status_for_user_id(user_id) for user_id in user_ids]}


@app.get("/api/tasks/{user_id}")
def read_task_state(user_id: str) -> dict[str, Any]:
    user_id = sanitize_user_id(user_id)
    task_dir = get_task_dir(user_id)
    state = read_json(state_path(task_dir))
    if not state:
        raise HTTPException(status_code=404, detail="未找到任务")
    return with_dynamic_remaining(state)


@app.get("/api/tasks/{user_id}/qr")
def get_qr(user_id: str) -> FileResponse:
    user_id = sanitize_user_id(user_id)
    path = qr_path(get_task_dir(user_id))
    if not path.exists():
        raise HTTPException(status_code=404, detail="二维码尚未生成")
    return FileResponse(path, media_type="image/png")


@app.get("/api/tasks/{user_id}/events")
def task_events(user_id: str) -> StreamingResponse:
    user_id = sanitize_user_id(user_id)

    def event_stream():
        last_payload = None
        while True:
            state = read_json(state_path(get_task_dir(user_id)))
            if not state:
                yield "event: error\ndata: {\"message\":\"未找到任务\"}\n\n"
                return
            state = with_dynamic_remaining(state)
            payload = json.dumps(state, ensure_ascii=False)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if state.get("status") in {"success", "failed", "canceled"}:
                return
            time.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.on_event("startup")
def resume_waiting_tasks() -> None:
    if not DATA_DIR.exists():
        return
    for path in DATA_DIR.glob("*/state.json"):
        state = read_json(path, {})
        user_id = state.get("user_id")
        if not user_id or state.get("status") not in {"logged_in", "waiting_seckill"}:
            continue
        update_state(
            user_id,
            status="failed",
            message="服务重启后浏览器会话已失效，请重新开始抢购",
            browser_alive=False,
            remaining_seconds=0,
        )
