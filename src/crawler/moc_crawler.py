"""
文化部公共藝術爬蟲 v2
使用 Playwright + 正規表達式解析 Angular SPA
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright

BASE_URL = "https://publicart.moc.gov.tw"
WORK_LIST_URL = f"{BASE_URL}/home/zh-tw/works"
OUTPUT_DIR = Path("data/raw/moc")
METADATA_FILE = OUTPUT_DIR / "metadata.jsonl"
ERROR_LOG = OUTPUT_DIR / "errors.log"


@dataclass
class Artwork:
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


def parse_work_html(html: str, work_id: str) -> Optional[Artwork]:
    """解析作品頁面 HTML"""
    
    # 提取標題 (evtitle class in Angular)
    name_match = re.search(r'class="evtitle"[^>]*>([^<]+)', html)
    name = name_match.group(1).strip() if name_match else ""
    
    # 提取作者 (info class after evtitle)
    artist_match = re.search(r'class="evtitle"[^>]*>.*?class="info"[^>]*>([^<]+)', html, re.DOTALL)
    artist = artist_match.group(1).strip() if artist_match else ""
    
    # 提取描述
    desc_match = re.search(r'class="label"[^>]*>簡述[/／]</p[^>]*><p[^>]*>([^<]+)', html)
    description = desc_match.group(1).strip() if desc_match else ""
    
    # 提取圖片 URL - 優先取 publicartap.moc.gov.tw 的圖片 (第二張，通常是作品圖)
    # 格式: https://publicartap.moc.gov.tw/upload/image/年份/UUID/檔名
    img_matches = re.findall(r'(https://publicartap\.moc\.gov\.tw/upload/image/[^"\']+\.(?:jpg|jpeg|png|gif|webp))', html)
    image_url = img_matches[0] if img_matches else ""
    
    # 提取中繼資料 (興辦機關/年代/尺寸/材質/地點/經費/取得方式)
    text_block_match = re.search(r'興辦機關(?:[^／]*／){0,5}([^<]+)', html)
    
    # 用正規表達式解析各欄位
    year_match = re.search(r'創作年代[/／]\s*(\d{4})', html)
    year = year_match.group(1) if year_match else ""
    
    size_match = re.search(r'尺寸[/／]\s*([^地點經費材質取得方式]+)', html)
    size = size_match.group(1).strip() if size_match else ""
    
    material_match = re.search(r'材質[/／]\s*([^地點經費取得方式]+)', html)
    material = material_match.group(1).strip() if material_match else ""
    
    location_match = re.search(r'地點[/／]\s*([^經費取得方式]+)', html)
    location = location_match.group(1).strip() if location_match else ""
    
    budget_match = re.search(r'經費[/／]\s*(\d+)', html)
    budget = budget_match.group(1) if budget_match else ""
    
    county_match = re.search(r'([^\s]+(?:縣|市))[所在置於]', location)
    county = county_match.group(1).strip() if county_match else ""
    
    return Artwork(
        work_id=work_id,
        name=name,
        artist=artist,
        county=county,
        year=year,
        category="",  # 需從列表頁取得
        description=description,
        material=material,
        size=size,
        location=location,
        budget=budget,
        image_url=image_url,
        source_url=f"{WORK_LIST_URL}/{work_id}",
        created_at=datetime.now().isoformat()
    )


async def get_work_ids_from_page(browser, max_pages: int = 5) -> list[str]:
    """取得作品 ID 列表"""
    work_ids = []
    
    async with async_playwright() as p:
        browser_instance = await p.chromium.launch(headless=True)
        context = await browser_instance.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()
        
        for page_num in range(1, max_pages + 1):
            print(f"取得作品列表頁 {page_num}...")
            
            try:
                url = f"{WORK_LIST_URL}?page={page_num}" if page_num > 1 else WORK_LIST_URL
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                # 等待 Angular 載入
                await asyncio.sleep(2)
                
                # 等待作品連結出現
                try:
                    await page.wait_for_selector('a[href*="/works/"]', timeout=10000)
                except:
                    print(f"  頁面 {page_num} 無法載入作品列表")
                    continue
                
                # 提取所有作品 ID
                links = await page.query_selector_all('a[href*="/works/"]')
                page_ids = []
                for link in links:
                    href = await link.get_attribute('href')
                    if href:
                        match = re.search(r'/works/(\d+)', href)
                        if match:
                            page_ids.append(match.group(1))
                
                work_ids.extend(page_ids)
                print(f"  頁面 {page_num}: 新增 {len(page_ids)} 個作品")
                
            except Exception as e:
                print(f"取得頁面 {page_num} 失敗: {e}")
                continue
        
        await context.close()
        await browser_instance.close()
    
    # 去重
    return list(set(work_ids))


async def crawl_single_work(browser, work_id: str) -> Optional[Artwork]:
    """爬取單一作品詳細資訊"""
    url = f"{WORK_LIST_URL}/{work_id}"
    
    try:
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()
        
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        if response.status != 200:
            await context.close()
            return None
        
        # 等待 Angular 渲染
        await asyncio.sleep(2)
        
        # 等待主要內容
        try:
            await page.wait_for_selector('.evtitle', timeout=10000)
        except:
            pass
        
        html = await page.content()
        artwork = parse_work_html(html, work_id)
        
        await context.close()
        return artwork
        
    except Exception as e:
        return None


async def download_image(image_url: str, save_path: Path) -> bool:
    """下載圖片"""
    if not image_url:
        return False
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(image_url)
            if response.status_code == 200:
                save_path.write_bytes(response.content)
                return True
    except:
        pass
    return False


async def run_crawler(max_works: int = 100, max_pages: int = 5):
    """執行爬蟲主程式"""
    print("=" * 60)
    print("文化部公共藝術爬蟲 v2")
    print("=" * 60)
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "images").mkdir(exist_ok=True)
    
    print(f"\n📂 輸出目錄: {OUTPUT_DIR}")
    
    # 取得作品 ID
    print("\n🔍 取得作品列表...")
    work_ids = await get_work_ids_from_page(None, max_pages)
    print(f"\n✅ 取得 {len(work_ids)} 個作品 ID")
    
    work_ids = list(set(work_ids))[:max_works]
    print(f"📦 準備爬取 {len(work_ids)} 個作品...")
    
    # 啟動瀏覽器
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        results = []
        for i, work_id in enumerate(work_ids):
            print(f"\n[{i+1}/{len(work_ids)}] 爬取作品 {work_id}...")
            
            artwork = await crawl_single_work(browser, work_id)
            if artwork:
                results.append(artwork)
                print(f"  ✅ {artwork.name} - {artwork.artist}")
                
                # 下載圖片
                if artwork.image_url:
                    img_filename = f"{work_id}_{artwork.name[:20].replace('/','_')}.jpg"
                    img_path = OUTPUT_DIR / "images" / img_filename
                    if await download_image(artwork.image_url, img_path):
                        print(f"  🖼️ 圖片: {img_filename}")
            else:
                with open(ERROR_LOG, 'a') as f:
                    f.write(f"{work_id}\n")
                print(f"  ❌ 失敗")
            
            if (i + 1) % 10 == 0:
                print(f"\n😴 休息 2 秒...")
                await asyncio.sleep(2)
        
        await browser.close()
    
    # 儲存結果
    print(f"\n💾 儲存 {len(results)} 筆資料...")
    
    with open(METADATA_FILE, 'w', encoding='utf-8') as f:
        for artwork in results:
            f.write(json.dumps(asdict(artwork), ensure_ascii=False) + '\n')
    
    print(f"✅ 完成！")
    print(f"📄 資料: {METADATA_FILE}")
    print(f"📷 圖片: {OUTPUT_DIR / 'images'}")
    
    return results


if __name__ == "__main__":
    import sys
    
    max_works = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    
    asyncio.run(run_crawler(max_works=max_works, max_pages=max_pages))
