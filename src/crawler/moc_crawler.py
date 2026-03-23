"""
文化部公共藝術爬蟲
Crawl public art works from moc.gov.tw

使用方法:
    python -m src.crawler.moc_crawler
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://publicart.moc.gov.tw"
WORK_LIST_URL = f"{BASE_URL}/home/zh-tw/works"
OUTPUT_DIR = Path("data/raw/moc")
METADATA_FILE = OUTPUT_DIR / "metadata.jsonl"
ERROR_LOG = OUTPUT_DIR / "errors.log"


@dataclass
class Artwork:
    """藝術品資料結構"""
    work_id: str
    name: str
    artist: str
    county: str
    year: str
    category: str
    description: str
    material: str
    size: str
    location: str
    budget: str
    image_url: str = ""
    source_url: str = ""
    created_at: str = ""


def parse_work_page(html: str, work_id: str) -> Optional[Artwork]:
    """解析作品頁面"""
    soup = BeautifulSoup(html, 'html.parser')
    
    try:
        # 取得標題和作者
        title_elem = soup.find('h2') or soup.find('h3') or soup.find('h4')
        if title_elem:
            title_text = title_elem.get_text(strip=True)
            # 格式通常是 "作品名稱 作者名"
            parts = title_text.split()
            name = parts[0] if parts else ""
            artist = parts[1] if len(parts) > 1 else ""
        else:
            name = ""
            artist = ""
        
        # 取得描述
        desc_elem = soup.find('p')
        description = desc_elem.get_text(strip=True) if desc_elem else ""
        
        # 取得中繼資料
        text = soup.get_text()
        
        # 解析各欄位
        county = extract_field(text, '縣市') or extract_field(text, '地點')
        year = extract_field(text, '年代') or extract_field(text, '創作年代')
        category = extract_field(text, '種類') or ""
        material = extract_field(text, '材質')
        size = extract_field(text, '尺寸')
        location = extract_field(text, '地點')
        budget = extract_field(text, '經費')
        
        # 嘗試找圖片
        image_url = ""
        img_tags = soup.find_all('img')
        for img in img_tags:
            src = img.get('src', '')
            if 'upload' in src or 'image' in src or 'photo' in src:
                if not any(x in src for x in ['logo', 'icon', 'banner']):
                    image_url = src if src.startswith('http') else f"{BASE_URL}{src}"
                    break
        
        return Artwork(
            work_id=work_id,
            name=name,
            artist=artist,
            county=county,
            year=year,
            category=category,
            description=description,
            material=material or "",
            size=size or "",
            location=location or "",
            budget=budget or "",
            image_url=image_url,
            source_url=f"{WORK_LIST_URL}/{work_id}",
            created_at=datetime.now().isoformat()
        )
    except Exception as e:
        print(f"解析作品 {work_id} 失敗: {e}")
        return None


def extract_field(text: str, field_name: str) -> str:
    """從文字中提取欄位"""
    patterns = [
        rf'{field_name}／([^<\n]+)',
        rf'{field_name}：([^<\n]+)',
        rf'{field_name}:\s*([^\n]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


async def get_work_ids(page, max_pages: int = 10) -> list[str]:
    """取得作品 ID 列表"""
    work_ids = []
    
    for page_num in range(1, max_pages + 1):
        print(f"取得作品列表頁 {page_num}...")
        
        try:
            if page_num == 1:
                await page.goto(WORK_LIST_URL, wait_until="networkidle", timeout=30000)
            else:
                # 嘗試點擊分頁或載入更多
                await page.goto(f"{WORK_LIST_URL}?page={page_num}", wait_until="networkidle", timeout=30000)
            
            await asyncio.sleep(2)
            
            # 等待作品列表載入
            try:
                await page.wait_for_selector('a[href*="/works/"]', timeout=10000)
            except:
                print(f"頁面 {page_num} 無法載入作品列表")
                continue
            
            # 抓取作品連結
            links = await page.query_selector_all('a[href*="/works/"]')
            for link in links:
                href = await link.get_attribute('href')
                if href and '/works/' in href:
                    match = re.search(r'/works/(\d+)', href)
                    if match:
                        work_ids.append(match.group(1))
            
            print(f"  頁面 {page_num}: 新增 {len(links)} 個作品")
            
        except Exception as e:
            print(f"取得頁面 {page_num} 失敗: {e}")
            continue
    
    return list(set(work_ids))


async def crawl_work_detail(browser, work_id: str) -> Optional[Artwork]:
    """爬取單一作品詳細資訊"""
    url = f"{WORK_LIST_URL}/{work_id}"
    
    try:
        context = await browser.new_context()
        page = await context.new_page()
        
        response = await page.goto(url, timeout=30000)
        
        if response.status != 200:
            print(f"  作品 {work_id} HTTP {response.status}")
            await context.close()
            return None
        
        # 等待內容載入
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(1)
        
        html = await page.content()
        artwork = parse_work_page(html, work_id)
        
        await context.close()
        return artwork
        
    except Exception as e:
        print(f"  作品 {work_id} 失敗: {e}")
        return None


async def download_image(artwork: Artwork, save_dir: Path) -> str:
    """下載作品圖片"""
    if not artwork.image_url:
        return ""
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(artwork.image_url, timeout=30)
            if response.status_code == 200:
                ext = artwork.image_url.split('.')[-1].split('?')[0][:4]
                if not ext in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                    ext = 'jpg'
                filename = f"{artwork.work_id}_{artwork.name[:20]}.{ext}"
                filepath = save_dir / filename
                
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                
                return str(filepath)
    except Exception as e:
        print(f"  圖片下載失敗: {e}")
    
    return ""


async def run_crawler(max_works: int = 100, max_pages: int = 10):
    """執行爬蟲主程式"""
    print("=" * 60)
    print("文化部公共藝術爬蟲")
    print("=" * 60)
    
    # 建立輸出目錄
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "images").mkdir(exist_ok=True)
    
    print(f"\n📂 輸出目錄: {OUTPUT_DIR}")
    
    # 使用 Playwright 取得作品列表
    print("\n🔍 取得作品列表...")
    
    work_ids = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        # 取得作品 ID 列表
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()
        
        work_ids = await get_work_ids(page, max_pages)
        print(f"\n✅ 取得 {len(work_ids)} 個作品 ID")
        
        # 只取前面的
        work_ids = work_ids[:max_works]
        print(f"📦 準備爬取 {len(work_ids)} 個作品...")
        
        await context.close()
        
        # 逐一爬取詳細資訊
        results = []
        for i, work_id in enumerate(work_ids):
            print(f"\n[{i+1}/{len(work_ids)}] 爬取作品 {work_id}...")
            
            artwork = await crawl_work_detail(browser, work_id)
            if artwork:
                results.append(artwork)
                print(f"  ✅ {artwork.name} - {artwork.artist}")
                
                # 下載圖片
                if artwork.image_url:
                    img_path = await download_image(artwork, OUTPUT_DIR / "images")
                    if img_path:
                        print(f"  🖼️ 圖片: {img_path}")
            else:
                with open(ERROR_LOG, 'a') as f:
                    f.write(f"{work_id}\n")
            
            # 每 10 個作品休息一下
            if (i + 1) % 10 == 0:
                print("\n😴 休息 3 秒...")
                await asyncio.sleep(3)
        
        await browser.close()
    
    # 儲存結果
    print(f"\n💾 儲存 {len(results)} 筆資料...")
    
    with open(METADATA_FILE, 'w', encoding='utf-8') as f:
        for artwork in results:
            f.write(json.dumps(asdict(artwork), ensure_ascii=False) + '\n')
    
    print(f"✅ 完成！資料已儲存到 {METADATA_FILE}")
    print(f"❌ 錯誤日誌: {ERROR_LOG}")
    
    return results


if __name__ == "__main__":
    import sys
    
    max_works = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    asyncio.run(run_crawler(max_works=max_works, max_pages=max_pages))
