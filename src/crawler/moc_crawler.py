"""
文化部公共藝術爬蟲 v2
===================================
使用 Playwright + 正規表達式解析 Angular SPA 動態網站

功能：
- 取得文化部公共藝術網站的作品列表（分頁）
- 爬取每件作品的詳細資訊（名稱、作者、年代、材質、地點、經費等）
- 下載作品圖片

資料來源：https://publicart.moc.gov.tw/home/zh-tw/works
輸出目錄：data/raw/moc/

使用方法：
    python -m src.crawler.moc_crawler [最大作品數] [最大頁數]
    python -m src.crawler.moc_crawler 100 5   # 爬取 100 件作品，掃描 5 頁列表
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass, asdict   # dataclass: 定義 Artwork 資料結構
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright

# =============================================================================
# 全域常數
# =============================================================================

BASE_URL = "https://publicart.moc.gov.tw"                                    # 文化部公共藝術網站
WORK_LIST_URL = f"{BASE_URL}/home/zh-tw/works"                              # 作品列表頁 URL
OUTPUT_DIR = Path("data/raw/moc")                                            # 輸出根目錄
METADATA_FILE = OUTPUT_DIR / "metadata.jsonl"                                # 中繼資料輸出檔（JSONL 格式）
ERROR_LOG = OUTPUT_DIR / "errors.log"                                        # 失敗作品 ID 記錄檔


# =============================================================================
# 資料結構
# =============================================================================

@dataclass
class Artwork:
    """
    公共藝術作品資料結構

    欄位說明：
    - work_id: 作品在文化部資料庫中的唯一 ID
    - name: 作品名稱
    - artist: 作者/創作者名稱
    - county: 所在縣市
    - year: 創作/設置年份
    - category: 作品類別（壁畫、雕塑、浮雕等）
    - description: 作品簡述
    - material: 作品材質
    - size: 作品尺寸
    - location: 設置地點（完整地址）
    - budget: 經費（單位：元）
    - image_url: 作品圖片的下載網址
    - source_url: 原始作品頁面 URL
    - created_at: 資料爬取時間（ISO 格式）
    """
    work_id: str
    name: str
    artist: str
    county: str
    year: str
    category: str = ""
    description: str = ""
    material: str = ""
    size: str = ""
    location: str = ""
    budget: str = ""
    image_url: str = ""
    source_url: str = ""
    created_at: str = ""


# =============================================================================
# HTML 解析函式
# =============================================================================

def parse_work_html(html: str, work_id: str) -> Optional[Artwork]:
    """
    解析文化部作品頁面的 HTML，提取作品資訊。

    解析方式：使用正規表達式比對 HTML 中的特定 class 與結構。
    注意：文化部網站使用 Angular 框架，內容為動態渲染，
         因此需要等待 JavaScript 執行完畢後再取 HTML。

    Args:
        html (str): 完整的 HTML 字串（從瀏覽器取得）
        work_id (str): 作品 ID（用於組合 source_url）

    Returns:
        Artwork: 包含作品資訊的 Artwork 物件，若解析失敗則回傳 None
    """
    # --- 作品名稱：HTML class="evtitle"（Angular 動態內容）---
    name_match = re.search(r'class="evtitle"[^>]*>([^<]+)', html)
    name = name_match.group(1).strip() if name_match else ""

    # --- 作者名稱：evtitle 同區塊後的 info class ---
    artist_match = re.search(r'class="evtitle"[^>]*>.*?class="info"[^>]*>([^<]+)', html, re.DOTALL)
    artist = artist_match.group(1).strip() if artist_match else ""

    # --- 作品簡述：class="label" 包含「簡述」關鍵字後的 <p> 標籤內容 ---
    desc_match = re.search(r'class="label"[^>]*>簡述[/／]</p[^>]*><p[^>]*>([^<]+)', html)
    description = desc_match.group(1).strip() if desc_match else ""

    # --- 圖片 URL：優先取 publicartap.moc.gov.tw 的圖片（第二張，通常是作品主圖）---
    # 格式: https://publicartap.moc.gov.tw/upload/image/年份/UUID/檔名.jpg
    img_matches = re.findall(
        r'(https://publicartap\.moc\.gov\.tw/upload/image/[^"\']+\.(?:jpg|jpeg|png|gif|webp))',
        html
    )
    image_url = img_matches[0] if img_matches else ""

    # --- 各項中繼資料：透過「關鍵字/數值」模式擷取 ---
    year_match = re.search(r'創作年代[/／]\s*(\d{4})', html)              # 例：創作年代/2025
    year = year_match.group(1) if year_match else ""

    size_match = re.search(r'尺寸[/／]\s*([^地點經費材質取得方式]+)', html) # 例：尺寸/210×130×320 cm
    size = size_match.group(1).strip() if size_match else ""

    material_match = re.search(r'材質[/／]\s*([^地點經費取得方式]+)', html) # 例：材質/銅、烤漆
    material = material_match.group(1).strip() if material_match else ""

    location_match = re.search(r'地點[/／]\s*([^經費取得方式]+)', html)     # 例：地點/營區大門東側空地
    location = location_match.group(1).strip() if location_match else ""

    budget_match = re.search(r'經費[/／]\s*(\d+)', html)                   # 例：經費/2330000
    budget = budget_match.group(1) if budget_match else ""

    # --- 縣市：從地點欄位中提取「XX縣」或「XX市」---
    county_match = re.search(r'([^\s]+(?:縣|市))[所在置於]', location)
    county = county_match.group(1).strip() if county_match else ""

    return Artwork(
        work_id=work_id,
        name=name,
        artist=artist,
        county=county,
        year=year,
        category="",                                                         # 待從列表頁取得
        description=description,
        material=material,
        size=size,
        location=location,
        budget=budget,
        image_url=image_url,
        source_url=f"{WORK_LIST_URL}/{work_id}",
        created_at=datetime.now().isoformat()
    )


# =============================================================================
# 爬蟲核心函式
# =============================================================================

async def get_work_ids_from_page(max_pages: int = 5) -> list[str]:
    """
    取得作品 ID 列表（分頁掃描作品列表頁）。

    從首頁開始，逐一拜訪每一頁，收集所有作品的 /works/{ID} 連結。

    Args:
        max_pages (int): 最多掃描幾頁列表，預設 5 頁

    Returns:
        list[str]: 作品 ID 清單（可能有重複，需要去重）
    """
    work_ids = []

    async with async_playwright() as p:
        # 啟動無頭 Chromium 瀏覽器（無 UI，節省資源）
        browser_instance = await p.chromium.launch(headless=True)
        context = await browser_instance.new_context(
            # 模擬一般 Mac Safari 瀏覽器，避免被網站阻擋
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        for page_num in range(1, max_pages + 1):
            print(f"取得作品列表頁 {page_num}...")

            try:
                # 第 1 页使用首頁 URL，第 2 頁以上使用 ?page=N 參數
                url = f"{WORK_LIST_URL}?page={page_num}" if page_num > 1 else WORK_LIST_URL
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # 等待 Angular 動態內容渲染（JavaScript 執行需要時間）
                await asyncio.sleep(2)

                # 等待作品連結出現（最多等 10 秒）
                try:
                    await page.wait_for_selector('a[href*="/works/"]', timeout=10000)
                except:
                    print(f"  ⚠️ 頁面 {page_num} 無法載入作品列表")
                    continue

                # 取出所有包含 /works/ ID 的連結
                links = await page.query_selector_all('a[href*="/works/"]')
                page_ids = []
                for link in links:
                    href = await link.get_attribute('href')                  # 取得 href 屬性
                    if href:
                        match = re.search(r'/works/(\d+)', href)            # 從 URL 抽出數字 ID
                        if match:
                            page_ids.append(match.group(1))

                work_ids.extend(page_ids)
                print(f"  頁面 {page_num}: 新增 {len(page_ids)} 個作品")

            except Exception as e:
                print(f"取得頁面 {page_num} 失敗: {e}")
                continue

        await context.close()
        await browser_instance.close()

    # 去重（同一作品可能在多處出現）
    return list(set(work_ids))


async def crawl_single_work(work_id: str) -> Optional[Artwork]:
    """
    爬取單一作品詳細資訊。

    打開作品頁面，等待 Angular 渲染完成，取 HTML 呼叫 parse_work_html() 解析。

    Args:
        work_id (str): 作品 ID

    Returns:
        Artwork: 作品資料，若失敗則回傳 None
    """
    url = f"{WORK_LIST_URL}/{work_id}"

    try:
        context = await httpx.AsyncClient().acr()
        browser_context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await browser_context.new_page()

        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # HTTP 狀態碼不是 200 代表頁面不存在
        if response.status != 200:
            await browser_context.close()
            return None

        # 等待 Angular 渲染
        await asyncio.sleep(2)

        # 等待主要內容元素 .evtitle 出現
        try:
            await page.wait_for_selector('.evtitle', timeout=10000)
        except:
            pass                                                    # 容錯：沒等到也繼續

        # 取得完整 HTML 並解析
        html = await page.content()
        artwork = parse_work_html(html, work_id)

        await browser_context.close()
        return artwork

    except Exception as e:
        return None


async def download_image(image_url: str, save_path: Path) -> bool:
    """
    非同步下載圖片至指定路徑。

    Args:
        image_url (str): 圖片網址
        save_path (Path): 儲存路徑（包含檔名）

    Returns:
        bool: 下載成功回傳 True，失敗回傳 False
    """
    if not image_url:
        return False

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(image_url)
            if response.status_code == 200:
                save_path.write_bytes(response.content)            # 寫入二進位檔案
                return True
    except:
        pass
    return False


# =============================================================================
# 主程式
# =============================================================================

async def run_crawler(max_works: int = 100, max_pages: int = 5):
    """
    執行爬蟲主程式。

    流程：
    1. 建立輸出目錄
    2. 取得作品 ID 列表
    3. 逐一爬取作品詳細資訊
    4. 下載作品圖片
    5. 儲存 JSONL 中繼資料

    Args:
        max_works (int): 最多爬取幾件作品，預設 100
        max_pages (int): 最多掃描幾頁列表，預設 5
    """
    print("=" * 60)
    print("文化部公共藝術爬蟲 v2")
    print("=" * 60)

    # 建立輸出目錄結構
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "images").mkdir(exist_ok=True)

    print(f"\n📂 輸出目錄: {OUTPUT_DIR}")

    # 1. 取得作品 ID 列表
    print("\n🔍 取得作品列表...")
    work_ids = await get_work_ids_from_page(max_pages)
    print(f"\n✅ 取得 {len(work_ids)} 個作品 ID")

    # 取前面 max_works 件（並去重）
    work_ids = list(set(work_ids))[:max_works]
    print(f"📦 準備爬取 {len(work_ids)} 個作品...")

    # 2. 啟動瀏覽器，逐一爬取
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        results = []
        for i, work_id in enumerate(work_ids):
            print(f"\n[{i+1}/{len(work_ids)}] 爬取作品 {work_id}...")

            artwork = await crawl_single_work(work_id)
            if artwork:
                results.append(artwork)
                print(f"  ✅ {artwork.name} - {artwork.artist}")

                # 3. 下載圖片
                if artwork.image_url:
                    # 檔名：ID_作品名稱.jpg，避免中文檔名問題
                    safe_name = artwork.name[:20].replace('/', '_').replace('\\', '_')
                    img_filename = f"{work_id}_{safe_name}.jpg"
                    img_path = OUTPUT_DIR / "images" / img_filename
                    if await download_image(artwork.image_url, img_path):
                        print(f"  🖼️ 圖片: {img_filename}")
            else:
                # 記錄失敗的 ID 到 error log
                with open(ERROR_LOG, 'a') as f:
                    f.write(f"{work_id}\n")
                print(f"  ❌ 解析失敗")

            # 每 10 件休息 2 秒，避免對伺服器造成負擔
            if (i + 1) % 10 == 0:
                print(f"\n😴 休息 2 秒...")
                await asyncio.sleep(2)

        await browser.close()

    # 4. 儲存 JSONL 中繼資料
    print(f"\n💾 儲存 {len(results)} 筆資料...")

    with open(METADATA_FILE, 'w', encoding='utf-8') as f:
        for artwork in results:
            # 每行一筆 JSON，方便日後用 jq 或 python jsonl 工具處理
            f.write(json.dumps(asdict(artwork), ensure_ascii=False) + '\n')

    print(f"✅ 完成！")
    print(f"📄 資料: {METADATA_FILE}")
    print(f"📷 圖片: {OUTPUT_DIR / 'images'}")

    return results


if __name__ == "__main__":
    import sys

    # 支援命令列參數：python moc_crawler.py [最大作品數] [最大頁數]
    max_works = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    asyncio.run(run_crawler(max_works=max_works, max_pages=max_pages))
